import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import fitz  # PyMuPDF
import re
import shutil
import json
from dataclasses import asdict, dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from backend.libs.config import (
    PDF_VISION_SCAN,
    PDF_TEXT_REFINE,
    PDF_VISION_AUDIT,
    PDF_FIGURE_VERIFY,
    PDF_VISION_MAX_WORKERS,
    call_vision_api,
    call_vision_api_multi,
    get_chat_client,
)

# Unpack per-task (provider, model) tuples
PDF_VISION_SCAN_PROVIDER, PDF_VISION_SCAN_MODEL = PDF_VISION_SCAN
PDF_TEXT_REFINE_PROVIDER, PDF_TEXT_REFINE_MODEL = PDF_TEXT_REFINE
PDF_VISION_AUDIT_PROVIDER, PDF_VISION_AUDIT_MODEL = PDF_VISION_AUDIT
PDF_FIGURE_VERIFY_PROVIDER, PDF_FIGURE_VERIFY_MODEL = PDF_FIGURE_VERIFY

# Text window limit
MAX_REFINE_TOKENS = 25000

# ─── Figure verification helpers ─────────────────────────────────

MAX_EXPAND_RATIO = 0.30  # 30% max expansion per edge
MAX_COMPOSITE_EXPAND_RATIO = 1.00  # composite screenshots may need large table/interface expansion
MIN_CROP_DIM = 50         # minimum viable crop dimension in pixels
NATIVE_TEXT_MIN_CHARS = 80
FULL_PAGE_IMAGE_THRESHOLD = 0.72
MIN_DEDUP_PROTECTED_AREA = 0.10
UNTRANSCRIBED_PLACEHOLDER_PATTERNS = [
    "未转录",
    "not transcribed",
    "not transcription",
    "无法转录",
    "无结构化表格",
    "assumed part of visual region",
]


@dataclass
class RegionManifest:
    id: str
    kind: str
    source: str
    bbox: list[float]
    action: str
    confidence: float
    notes: list[str] = field(default_factory=list)
    path: str | None = None


@dataclass
class PageManifest:
    page: int
    page_type: str
    width: float
    height: float
    native_text_chars: int
    image_coverage: float
    regions: list[RegionManifest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _rect_to_bbox(rect, page_w: float, page_h: float) -> list[float]:
    return [
        round(max(0.0, min(1000.0, rect[0] / page_w * 1000)), 2),
        round(max(0.0, min(1000.0, rect[1] / page_h * 1000)), 2),
        round(max(0.0, min(1000.0, rect[2] / page_w * 1000)), 2),
        round(max(0.0, min(1000.0, rect[3] / page_h * 1000)), 2),
    ]


def _bbox_area(bbox: list[float]) -> float:
    if len(bbox) != 4:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _pixel_to_norm_bbox(left, top, right, bottom, width, height) -> list[float]:
    return [
        round(left / width * 1000, 2),
        round(top / height * 1000, 2),
        round(right / width * 1000, 2),
        round(bottom / height * 1000, 2),
    ]


def _is_composite_visual_hint(desc: str) -> bool:
    text = desc.lower()
    direct_hints = [
        "compositevisual", "composite visual", "instrument result", "visual region",
        "仪器结果", "复合视觉", "视觉区域",
    ]
    if any(hint in text for hint in direct_hints):
        return True
    hints = [
        "instrument", "screenshot", "screen", "interface", "thermo", "scientific",
        "chart", "curve", "plot", "spectrum", "graph", "table", "data",
        "仪器", "截图", "屏幕", "界面", "曲线", "图表", "谱图", "表格", "数据",
    ]
    return sum(1 for hint in hints if hint in text) >= 2


def _parse_verify_response(raw_text: str) -> dict | None:
    """Parse VLM verification response. Returns dict or None on any failure."""
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    # Schema validation
    required = {"is_complete", "is_pure", "missing_edges", "expand_ratios",
                "extra_content_edges", "shrink_ratios"}
    if not all(k in data for k in required):
        return None
    if not isinstance(data["is_complete"], bool) or not isinstance(data["is_pure"], bool):
        return None
    for k in ["expand_ratios", "shrink_ratios"]:
        if not isinstance(data[k], dict):
            return None
        for v in data[k].values():
            if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                return None
    return data


def _has_untranscribed_placeholder(text: str) -> bool:
    lowered = (text or "").lower()
    return any(pattern in lowered for pattern in UNTRANSCRIBED_PLACEHOLDER_PATTERNS)


def _apply_expand(left, top, right, bottom, page_w, page_h,
                  orig_w, orig_h, ratios, max_expand_ratio=MAX_EXPAND_RATIO):
    """Expand BBOX edges by given ratios (clamped to MAX_EXPAND_RATIO).
    All ratios are computed from ORIGINAL BBOX dimensions."""
    r_left = min(float(ratios.get("left", 0)), max_expand_ratio)
    r_top = min(float(ratios.get("top", 0)), max_expand_ratio)
    r_right = min(float(ratios.get("right", 0)), max_expand_ratio)
    r_bottom = min(float(ratios.get("bottom", 0)), max_expand_ratio)

    left = max(0, int(left - r_left * orig_w))
    top = max(0, int(top - r_top * orig_h))
    right = min(page_w, int(right + r_right * orig_w))
    bottom = min(page_h, int(bottom + r_bottom * orig_h))
    return left, top, right, bottom


def _apply_shrink(left, top, right, bottom, orig_w, orig_h, ratios):
    """Shrink BBOX edges by given ratios (computed from ORIGINAL dimensions).
    Enforces MIN_CROP_DIM minimum dimension."""
    r_left = float(ratios.get("left", 0))
    r_top = float(ratios.get("top", 0))
    r_right = float(ratios.get("right", 0))
    r_bottom = float(ratios.get("bottom", 0))

    adj_left = int(left + r_left * orig_w)
    adj_top = int(top + r_top * orig_h)
    adj_right = int(right - r_right * orig_w)
    adj_bottom = int(bottom - r_bottom * orig_h)

    if adj_right <= adj_left or adj_bottom <= adj_top:
        return left, top, right, bottom  # don't invert

    if (adj_right - adj_left) < MIN_CROP_DIM or (adj_bottom - adj_top) < MIN_CROP_DIM:
        return left, top, right, bottom  # revert to original

    return adj_left, adj_top, adj_right, adj_bottom


class PDFParser:
    def __init__(self, pdf_path, output_dir, enable_audit=False):
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        self.enable_audit = enable_audit
        
        self.images_dir = os.path.join(output_dir, "images")
        self.temp_dir = os.path.join(output_dir, "temp_render")
        
        self._ensure_dirs()
        self.doc = fitz.open(pdf_path)
        
        # 存储裁切后的图片路径: { page_index: [path_id0, path_id1, ...] }
        self.page_images_map = {} 
        self.page_manifests: list[PageManifest] = []
        self.page_render_paths: dict[int, str] = {}
        self.had_page_scan_error = False

    def _ensure_dirs(self):
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        if not os.path.exists(self.images_dir): os.makedirs(self.images_dir)
        if not os.path.exists(self.temp_dir): os.makedirs(self.temp_dir)

    def _render_page_as_image(self, page_index):
        """将PDF页面渲染为高分辨率图片 (用于裁剪)"""
        # 提高 DPI 以保证裁剪出来的图片清晰
        pix = self.doc[page_index].get_pixmap(dpi=300)
        image_path = os.path.join(self.temp_dir, f"page_render_{page_index}.png")
        pix.save(image_path)
        return image_path

    def _build_page_manifest(self, page_index: int) -> PageManifest:
        page = self.doc[page_index]
        page_w = float(page.rect.width or 1)
        page_h = float(page.rect.height or 1)
        text = page.get_text("text") or ""
        native_text_chars = len(text.strip())

        regions: list[RegionManifest] = []
        image_coverage = 0.0
        try:
            page_dict = page.get_text("dict")
            for block_index, block in enumerate(page_dict.get("blocks", [])):
                bbox = block.get("bbox")
                if not bbox:
                    continue
                norm_bbox = _rect_to_bbox(bbox, page_w, page_h)
                if block.get("type") == 1:
                    coverage = _bbox_area(norm_bbox) / 1_000_000
                    image_coverage += coverage
                    kind = "composite_visual" if coverage >= FULL_PAGE_IMAGE_THRESHOLD else "figure"
                    notes = ["native image block"]
                    if coverage >= FULL_PAGE_IMAGE_THRESHOLD:
                        notes.append("large image block; likely scanned/photo page")
                    regions.append(
                        RegionManifest(
                            id=f"p{page_index}_native_img{block_index}",
                            kind=kind,
                            source="native",
                            bbox=norm_bbox,
                            action="analyze_with_vlm" if coverage >= FULL_PAGE_IMAGE_THRESHOLD else "preserve_image",
                            confidence=0.75 if coverage >= FULL_PAGE_IMAGE_THRESHOLD else 0.6,
                            notes=notes,
                        )
                    )
        except Exception as e:
            regions.append(
                RegionManifest(
                    id=f"p{page_index}_analysis_error",
                    kind="ignored",
                    source="native",
                    bbox=[0, 0, 0, 0],
                    action="ignore",
                    confidence=0.0,
                    notes=[f"page structure analysis failed: {type(e).__name__}: {e}"],
                )
            )

        image_coverage = min(image_coverage, 1.0)
        if native_text_chars >= NATIVE_TEXT_MIN_CHARS and image_coverage < FULL_PAGE_IMAGE_THRESHOLD:
            page_type = "native"
        elif native_text_chars >= NATIVE_TEXT_MIN_CHARS:
            page_type = "mixed"
        elif image_coverage >= FULL_PAGE_IMAGE_THRESHOLD:
            page_type = "scanned"
        else:
            page_type = "unknown"

        notes = []
        if page_type == "scanned":
            notes.append("OCR still needed for body text; protect composite visual regions from duplication")
        if page_type in {"native", "mixed"}:
            notes.append("native PDF text can be trusted before visual OCR")
        if page_type in {"scanned", "unknown"}:
            for region in regions:
                if region.source == "native" and region.path is None and region.action == "preserve_image":
                    region.action = "analyze_with_vlm"
                    region.notes.append("page needs raster OCR; native image block is context only")

        return PageManifest(
            page=page_index,
            page_type=page_type,
            width=page_w,
            height=page_h,
            native_text_chars=native_text_chars,
            image_coverage=round(image_coverage, 4),
            regions=regions,
            notes=notes,
        )

    def _save_manifest(self, partial: bool = False):
        manifest_name = "manifest.partial.json" if partial else "manifest.json"
        manifest_path = os.path.join(self.output_dir, manifest_name)
        payload = {
            "pdf_path": self.pdf_path,
            "policy": {
                "native_text": "markdown",
                "native_table": "markdown_when_reliable",
                "raster_table": "preserve_inside_visual_region",
                "composite_visual": "preserve_image",
            },
            "pages": [asdict(item) for item in self.page_manifests],
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return manifest_path

    def _native_page_to_markdown(self, page_index: int, page_manifest: PageManifest, page_image_path: str) -> str:
        """Build a conservative Markdown page from native PDF text/image blocks."""
        page = self.doc[page_index]
        page_w = float(page.rect.width or 1)
        page_h = float(page.rect.height or 1)
        self.page_images_map[page_index] = []
        parts: list[str] = []

        try:
            rendered = Image.open(page_image_path)
            render_w, render_h = rendered.size
        except Exception as e:
            rendered = None
            render_w = render_h = 1
            page_manifest.notes.append(f"native image crop disabled: {type(e).__name__}: {e}")

        try:
            page_dict = page.get_text("dict")
        except Exception as e:
            page_manifest.notes.append(f"native block extraction failed: {type(e).__name__}: {e}")
            return page.get_text("text") or ""

        for block_index, block in enumerate(page_dict.get("blocks", [])):
            block_type = block.get("type")
            if block_type == 0:
                lines = []
                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    if line_text:
                        lines.append(line_text)
                if lines:
                    parts.append("\n".join(lines))
            elif block_type == 1 and rendered is not None:
                bbox = block.get("bbox")
                if not bbox:
                    continue
                left = int(max(0, bbox[0] / page_w * render_w))
                top = int(max(0, bbox[1] / page_h * render_h))
                right = int(min(render_w, bbox[2] / page_w * render_w))
                bottom = int(min(render_h, bbox[3] / page_h * render_h))
                if (right - left) < 10 or (bottom - top) < 10:
                    continue

                img_seq = len(self.page_images_map[page_index])
                img_name = f"p{page_index}_native{img_seq}.png"
                save_path = os.path.join(self.images_dir, img_name)
                try:
                    rendered.crop((left, top, right, bottom)).save(save_path)
                except Exception as e:
                    page_manifest.notes.append(f"native image crop failed: {type(e).__name__}: {e}")
                    continue

                self.page_images_map[page_index].append(save_path)
                norm_bbox = _pixel_to_norm_bbox(left, top, right, bottom, render_w, render_h)
                area_ratio = _bbox_area(norm_bbox) / 1_000_000
                kind = "composite_visual" if area_ratio > 0.2 else "figure"
                desc = "CompositeVisual" if kind == "composite_visual" else "NativeImage"
                existing_region = next(
                    (
                        region
                        for region in page_manifest.regions
                        if region.id == f"p{page_index}_native_img{block_index}"
                    ),
                    None,
                )
                if existing_region:
                    existing_region.kind = kind
                    existing_region.bbox = norm_bbox
                    existing_region.path = os.path.relpath(save_path, self.output_dir)
                    existing_region.action = "preserve_image"
                    existing_region.confidence = max(existing_region.confidence, 0.85)
                    existing_region.notes.append("native block crop")
                else:
                    page_manifest.regions.append(
                        RegionManifest(
                            id=f"p{page_index}_native_crop{img_seq}",
                            kind=kind,
                            source="native",
                            bbox=norm_bbox,
                            action="preserve_image",
                            confidence=0.85,
                            notes=["native block crop", f"source_block={block_index}"],
                            path=os.path.relpath(save_path, self.output_dir),
                        )
                    )
                parts.append(f"![{desc}](IMG_P{page_index}_ID{img_seq})")

        return "\n\n".join(part for part in parts if part.strip())

    def _verify_crop(self, render_path, crop_path, target_desc="Figure"):
        """
        Verify crop completeness and purity using VLM with page render context.
        Returns: parsed dict with is_complete, is_pure, adjustments, or None on failure.
        """
        verify_provider, verify_model = PDF_FIGURE_VERIFY
        is_composite = _is_composite_visual_hint(target_desc)
        system_prompt = (
            "You are a precise image quality inspector. "
            "You receive a full page render and a cropped figure from that page. "
            "Your job: determine if the crop (1) contains the COMPLETE figure, and "
            "(2) does NOT include unrelated text or content outside the figure."
        )
        composite_rule = ""
        if is_composite:
            composite_rule = (
                "\n**Composite visual rule:** The intended crop is a CompositeVisual, not just a chart. "
                "It must include all visually attached instrument-result content: plot/curve, axes, "
                "legends, title, adjacent result/data table, and instrument interface labels that belong "
                "to the same result block. If the crop only contains the plot while an attached data table "
                "or result panel is visible next to it on the full page, mark is_complete=false and request "
                "expansion toward that missing table/panel.\n"
            )
        user_prompt = (
            "IMAGE 1 is a full page render. IMAGE 2 is a cropped figure extracted from that page.\n\n"
            f"Intended visual type: {target_desc or 'Figure'}.\n"
            f"{composite_rule}\n"
            "**Tasks:**\n"
            "1. **Completeness**: Compare the crop against the page render. Is any part of "
            "   the figure cut off at the crop edges? Check all four edges.\n"
            "2. **Purity**: Does the crop include body text, page numbers, or other content "
            "   that is clearly NOT part of this figure?\n\n"
            "**Output format (strict JSON, no markdown fences):**\n"
            '{"is_complete": true/false, "is_pure": true/false, '
            '"missing_edges": ["bottom","right",...], '
            '"expand_ratios": {"bottom": 0.05, "right": 0.03, ...}, '
            '"extra_content_edges": ["top",...], '
            '"shrink_ratios": {"top": 0.04, ...}}\n\n'
            "**Ratio rules:**\n"
            "- Ratios are fractions of the ORIGINAL (pre-adjustment) crop dimension (0.0 to 1.0)\n"
            "- For expand: 0.05 means 'expand by 5% of ORIGINAL width/height'\n"
            "- For shrink: 0.03 means 'shrink by 3% of ORIGINAL width/height'\n"
            "- Only include edges that need adjustment. Omit edges that are fine.\n"
            "- If is_complete is true, expand_ratios must be empty {}.\n"
            "- If is_pure is true, shrink_ratios must be empty {}."
        )
        try:
            raw = call_vision_api_multi(
                verify_provider, verify_model,
                system_prompt, user_prompt,
                [render_path, crop_path]
            )
            return _parse_verify_response(raw)
        except Exception as e:
            print(
                f"\n{'='*60}\n"
                f"[LLM ERROR] Figure Verification | provider={verify_provider} model={verify_model}\n"
                f"  {type(e).__name__}: {e}\n"
                f"{'='*60}\n",
                file=sys.stderr, flush=True
            )
        return None

    # ---------------- Phase 1: 视觉检测与 OCR ----------------

    def _phase1_vision_scan(self, image_path, page_num, page_manifest: PageManifest | None = None):
        """
        Phase 1: 视觉扫描 (回滚策略：稳定性优先)
        目标：只负责精准定位图片坐标，描述用简单的占位符，防止格式崩溃。
        """
        system_prompt = "You are a precise document layout analyzer."
        page_type = page_manifest.page_type if page_manifest else "unknown"
        user_prompt = (
            f"Analyze Page {page_num}.\n"
            f"Page source hint from PDF structure: {page_type}.\n"
            "**Task:** Extract readable report body text to Markdown and mark visual regions.\n"
            "**SOURCE FIDELITY RULES:**\n"
            "1. Transcribe ALL legible handwritten and printed report text into Markdown, including "
            "experiment data, calculations, explanations, discussions, and teacher/student notes. "
            "Never write placeholders such as 'handwritten part not transcribed' or 'not transcribed'. "
            "If a word is uncertain, transcribe the surrounding text and mark only that word as [uncertain].\n"
            "2. If a table is ordinary readable report text, including handwritten data tables, convert it to Markdown table text; "
            "do NOT also emit a BBOX for that table.\n"
            "3. If a table is inside a screenshot/photo/scan of an instrument result, DO NOT "
            "convert that internal table separately. Emit ONE BBOX around the whole composite "
            "visual region, including chart/curve/table/interface text that belong together.\n"
            "4. For Thermo SCIENTIFIC / instrument report screenshots, plots with attached data "
            "tables, spectra, diagrams, "
            "and apparatus images, output: `![CompositeVisual](BBOX: [x1, y1, x2, y2])` or "
            "`![Figure](BBOX: [x1, y1, x2, y2])`.\n"
            "5. After emitting a BBOX for a composite visual region, do not duplicate the text "
            "inside that same region in the Markdown body.\n"
            "6. If unsure whether a table is native report text or part of a screenshot/instrument "
            "output, prefer preserving it inside one CompositeVisual BBOX.\n"
            "**BBOX RULES:**\n"
            "7. BBOX must cover the ENTIRE visual region including labels, axes, legends, "
            "titles, attached result tables, and captions that are visually part of it.\n"
            "8. Do NOT include surrounding report paragraphs, handwritten explanations, page headers/footers, or page numbers.\n"
            "9. Coordinates are normalized 0-1000. (0,0)=top-left, (1000,1000)=bottom-right.\n"
            "10. Keep image descriptions short: CompositeVisual, Chart, Spectrum, Diagram, Apparatus.\n"
        )

        try:
            return call_vision_api(
                PDF_VISION_SCAN_PROVIDER, PDF_VISION_SCAN_MODEL,
                system_prompt, user_prompt, image_path
            )
        except Exception as e:
            self.had_page_scan_error = True
            print(
                f"\n{'='*60}\n"
                f"[LLM ERROR] PDF Phase 1 Vision Scan | provider={PDF_VISION_SCAN_PROVIDER} model={PDF_VISION_SCAN_MODEL}\n"
                f"  Page {page_num}  {type(e).__name__}: {e}\n"
                f"{'='*60}\n",
                file=sys.stderr, flush=True
            )
        return ""

    def _phase1_handwriting_rescan(self, image_path, page_num, previous_text):
        system_prompt = "You are a meticulous Chinese handwritten lab-report OCR transcriber."
        user_prompt = (
            f"Re-analyze Page {page_num}. The previous OCR output omitted handwritten content.\n\n"
            "**Previous incomplete output:**\n"
            f"{previous_text}\n\n"
            "**Corrective task:**\n"
            "1. Transcribe ALL legible handwritten and printed text into Markdown, especially sections like "
            "实验数据记录和处理、实验结果与分析、讨论/心得、手写计算、解释说明.\n"
            "2. Do NOT output placeholders such as '手写部分未转录', 'not transcribed', '无结构化表格', or "
            "'assumed part of visual region'.\n"
            "3. Handwritten data tables should be converted to Markdown tables when rows/columns are readable. "
            "If the table structure is unclear, use bullet lists but still transcribe the values and explanation.\n"
            "4. Only use BBOX for non-text visual material or instrument screenshots. Handwritten explanations "
            "are report text and must be OCR text, not an image placeholder.\n"
            "5. Preserve formulas and units. Mark isolated uncertain words as [uncertain] instead of dropping a whole section.\n"
        )
        try:
            return call_vision_api(
                PDF_VISION_SCAN_PROVIDER, PDF_VISION_SCAN_MODEL,
                system_prompt, user_prompt, image_path
            )
        except Exception as e:
            print(
                f"\n{'='*60}\n"
                f"[LLM ERROR] PDF Phase 1 Handwriting Rescan | provider={PDF_VISION_SCAN_PROVIDER} model={PDF_VISION_SCAN_MODEL}\n"
                f"  Page {page_num}  {type(e).__name__}: {e}\n"
                f"{'='*60}\n",
                file=sys.stderr, flush=True
            )
        return previous_text

    def _should_verify_crop(
        self,
        page_manifest: PageManifest | None,
        desc: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        width: int,
        height: int,
    ) -> tuple[bool, list[str]]:
        crop_w = right - left
        crop_h = bottom - top
        area_ratio = (crop_w * crop_h) / max(width * height, 1)
        margin = 0.03
        reasons = []
        if page_manifest and page_manifest.page_type in {"scanned", "unknown"}:
            reasons.append(f"page_type={page_manifest.page_type}")
        if _is_composite_visual_hint(desc):
            reasons.append("composite visual hint")
        if area_ratio > 0.45:
            reasons.append("large crop")
        if (
            left <= width * margin
            or top <= height * margin
            or right >= width * (1 - margin)
            or bottom >= height * (1 - margin)
        ):
            reasons.append("near page edge")
        if crop_w < 200 or crop_h < 200:
            reasons.append("small crop")
        return bool(reasons), reasons

    def _crop_and_replace_bboxes(self, raw_text, page_image_path, page_index, page_manifest: PageManifest | None = None):
        """Phase 1 post-processing: detect BBOX tags, crop, verify, replace."""
        bbox_pattern = re.compile(
            r'!\[(.*?)\]\(BBOX:\s*\[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\]\)',
            re.IGNORECASE | re.DOTALL
        )
        self.page_images_map[page_index] = []

        try:
            original_img = Image.open(page_image_path)
            width, height = original_img.size
        except Exception as e:
            print(f"  [Error] Failed to load image: {e}")
            return raw_text

        # --- Pass 1: collect all BBOX matches and crop them ---
        matches = list(bbox_pattern.finditer(raw_text))
        crop_records = []  # [(start, end, desc, saved_path, left, top, right, bottom, should_verify)]

        for match in matches:
            desc = match.group(1).strip().replace("\n", " ")
            x1, y1, x2, y2 = map(int, match.groups()[1:])
            left = int(x1 / 1000 * width)
            top = int(y1 / 1000 * height)
            right = int(x2 / 1000 * width)
            bottom = int(y2 / 1000 * height)
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)

            if (right - left) < 10 or (bottom - top) < 10:
                crop_records.append((match.start(), match.end(), desc, None,
                                     left, top, right, bottom, False))
                continue

            try:
                crop_img = original_img.crop((left, top, right, bottom))
                img_seq = len(self.page_images_map[page_index])
                img_name = f"p{page_index}_crop{img_seq}.png"
                save_path = os.path.join(self.images_dir, img_name)
                crop_img.save(save_path)
                self.page_images_map[page_index].append(save_path)
                norm_bbox = _pixel_to_norm_bbox(left, top, right, bottom, width, height)
                should_verify, verify_reasons = self._should_verify_crop(
                    page_manifest, desc, left, top, right, bottom, width, height
                )
                if page_manifest is not None:
                    area_ratio = _bbox_area(norm_bbox) / 1_000_000
                    kind = "composite_visual" if _is_composite_visual_hint(desc) or area_ratio > 0.2 else "figure"
                    source = "scanned" if page_manifest.page_type == "scanned" else "raster"
                    page_manifest.regions.append(
                        RegionManifest(
                            id=f"p{page_index}_vlm_img{img_seq}",
                            kind=kind,
                            source=source,
                            bbox=norm_bbox,
                            action="preserve_image",
                            confidence=0.7 if should_verify else 0.82,
                            notes=[
                                "vlm bbox",
                                f"description={desc}",
                                *(f"verify: {reason}" for reason in verify_reasons),
                            ],
                            path=os.path.relpath(save_path, self.output_dir),
                        )
                    )
                crop_records.append((match.start(), match.end(), desc, save_path,
                                     left, top, right, bottom, should_verify))
            except Exception as e:
                print(f"  [Error] Cropping failed: {e}")
                crop_records.append((match.start(), match.end(), desc, None,
                                     left, top, right, bottom, False))

        # --- Pass 2: concurrent verification of all crops ---
        adjustments = {}  # img_seq -> adjusted (left, top, right, bottom) or None

        def verify_one(img_seq, save_path):
            if save_path is None:
                return img_seq, None
            record = crop_records[img_seq]
            target_desc = record[2]
            result = self._verify_crop(page_image_path, save_path, target_desc)
            if result is None:
                return img_seq, None  # keep original
            orig_left, orig_top, orig_right, orig_bottom = record[4:8]
            orig_w = orig_right - orig_left
            orig_h = orig_bottom - orig_top
            need_adjust = False
            adj_left, adj_top, adj_right, adj_bottom = orig_left, orig_top, orig_right, orig_bottom
            max_expand_ratio = (
                MAX_COMPOSITE_EXPAND_RATIO
                if _is_composite_visual_hint(target_desc)
                else MAX_EXPAND_RATIO
            )

            if not result.get("is_complete"):
                ratios = result.get("expand_ratios", {})
                adj_left, adj_top, adj_right, adj_bottom = _apply_expand(
                    adj_left, adj_top, adj_right, adj_bottom,
                    width, height, orig_w, orig_h, ratios, max_expand_ratio=max_expand_ratio
                )
                need_adjust = True
            if not result.get("is_pure"):
                ratios = result.get("shrink_ratios", {})
                adj_left, adj_top, adj_right, adj_bottom = _apply_shrink(
                    adj_left, adj_top, adj_right, adj_bottom,
                    orig_w, orig_h, ratios
                )
                need_adjust = True
            if need_adjust:
                return img_seq, (adj_left, adj_top, adj_right, adj_bottom)
            return img_seq, None

        # Fire only uncertain verification calls concurrently.
        max_workers = max(1, PDF_VISION_MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(verify_one, i, rec[3]): i
                for i, rec in enumerate(crop_records) if rec[3] is not None and rec[8]
            }
            for future in as_completed(futures):
                try:
                    img_seq, adj = future.result()
                except Exception as e:
                    img_seq = futures[future]
                    print(f"    [Verify] p{page_index}_crop{img_seq}: future failed ({e}), kept original",
                          file=sys.stderr, flush=True)
                    adj = None
                adjustments[img_seq] = adj

        # --- Pass 3: apply corrections and rebuild text ---
        parts = []
        last_pos = 0

        for i, rec in enumerate(crop_records):
            start, end, desc, save_path = rec[0], rec[1], rec[2], rec[3]
            parts.append(raw_text[last_pos:start])
            last_pos = end

            if save_path is None:
                parts.append("")
                continue

            img_seq = len([r for r in crop_records[:i] if r[3] is not None])
            adj = adjustments.get(i)

            if adj:
                adj_left, adj_top, adj_right, adj_bottom = adj
                # Non-destructive: save adjusted to temp, validate, then replace
                try:
                    temp_path = save_path + ".adjusted.png"
                    adj_crop = original_img.crop((adj_left, adj_top, adj_right, adj_bottom))
                    adj_w = adj_right - adj_left
                    adj_h = adj_bottom - adj_top
                    if adj_w >= 10 and adj_h >= 10 and adj_w <= width and adj_h <= height:
                        adj_crop.save(temp_path)
                        os.replace(temp_path, save_path)  # atomic replace
                        region = self._find_region_by_image_id(page_index, img_seq)
                        if region is not None:
                            region.bbox = _pixel_to_norm_bbox(
                                adj_left, adj_top, adj_right, adj_bottom, width, height
                            )
                            region.confidence = max(region.confidence, 0.82)
                            region.notes.append("verified bbox adjusted")
                        print(f"    [Verify] p{page_index}_crop{img_seq}: adjusted BBOX applied")
                    else:
                        print(f"    [Verify] p{page_index}_crop{img_seq}: adjusted BBOX out of bounds, kept original")
                except Exception as e:
                    print(f"    [Verify] p{page_index}_crop{img_seq}: re-crop failed ({e}), kept original")
                    temp_path = save_path + ".adjusted.png"
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
            else:
                if rec[8]:
                    print(f"    [Verify] p{page_index}_crop{img_seq}: passed, kept original")
                else:
                    print(f"    [Verify] p{page_index}_crop{img_seq}: skipped by source-aware heuristic")

            parts.append(f"![{desc}](IMG_P{page_index}_ID{img_seq})")

        parts.append(raw_text[last_pos:])
        return "".join(parts)

    # ---------------- Phase 2: 全局重构 (Text Refine) ----------------
    
    def _phase2_global_refine(self, full_draft_text):
        if not full_draft_text.strip(): return ""
        print(f"  > Sending {len(full_draft_text)} chars to Global Refiner...")
        
        system_prompt = "You are a Senior Technical Editor. Reconstruct raw OCR text into perfect Markdown."
        user_prompt = (
            "**Task:** Fix headings, merge split paragraphs, remove page numbers.\n"
            "**Constraint:** DO NOT modify `(IMG_P{x}_ID{y})` tags.\n"
            "**Handwriting fidelity:** Do NOT replace handwritten content with placeholders such as "
            "'未转录', 'not transcribed', or '无结构化表格'. Keep transcribed handwritten calculations, "
            "explanations, and data in Markdown because they are needed for grading.\n"
            "**Input:**\n" + full_draft_text
        )
        
        try:
            client = get_chat_client(PDF_TEXT_REFINE_PROVIDER)
            resp = client.chat.completions.create(
                model=PDF_TEXT_REFINE_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
            )
            content = resp.choices[0].message.content
            if content:
                return content.replace("```markdown", "").replace("```", "").strip()
        except Exception as e:
            print(
                f"\n{'='*60}\n"
                f"[LLM ERROR] PDF Phase 2 Text Refine | provider={PDF_TEXT_REFINE_PROVIDER} model={PDF_TEXT_REFINE_MODEL}\n"
                f"  {type(e).__name__}: {e}\n"
                f"{'='*60}\n",
                file=sys.stderr, flush=True
            )
        return full_draft_text

    def _protected_image_ids(self) -> set[tuple[int, int]]:
        protected: set[tuple[int, int]] = set()
        pattern = re.compile(r"^p(\d+)_(?:vlm_img|native_crop)(\d+)$")
        for page_manifest in self.page_manifests:
            for region in page_manifest.regions:
                if region.action != "preserve_image":
                    continue
                note_text = " ".join(region.notes)
                is_protected_visual = (
                    region.kind == "composite_visual"
                    or _is_composite_visual_hint(note_text)
                )
                if not is_protected_visual:
                    continue
                area_ratio = _bbox_area(region.bbox) / 1_000_000
                if area_ratio < MIN_DEDUP_PROTECTED_AREA:
                    region.notes.append(
                        f"kept following markdown table because protected crop is small ({area_ratio:.3f})"
                    )
                    continue
                match = pattern.match(region.id)
                if not match:
                    continue
                protected.add((int(match.group(1)), int(match.group(2))))
        return protected

    @staticmethod
    def _is_markdown_table_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith("|") or "|" not in stripped[1:]:
            return False
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        return len(cells) >= 2 and any(cells)

    @staticmethod
    def _is_markdown_table_separator(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith("|") or "|" not in stripped[1:]:
            return False
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        return len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)

    def _table_after_image_anchors(self, refined_text: str) -> set[tuple[int, int]]:
        image_anchor = re.compile(r"!\[[^\]]*\]\(IMG_P(\d+)_ID(\d+)\)")
        lines = refined_text.splitlines()
        anchors: set[tuple[int, int]] = set()
        for i, line in enumerate(lines):
            match = image_anchor.search(line)
            if not match:
                continue
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if (
                j + 1 < len(lines)
                and self._is_markdown_table_line(lines[j])
                and self._is_markdown_table_separator(lines[j + 1])
            ):
                anchors.add((int(match.group(1)), int(match.group(2))))
        return anchors

    def _expand_small_protected_crops_with_following_tables(self, refined_text: str) -> None:
        table_after_ids = self._table_after_image_anchors(refined_text)
        if not table_after_ids:
            return

        expanded = 0
        for page_idx, img_seq in table_after_ids:
            region = self._find_region_by_image_id(page_idx, img_seq)
            if region is None or region.action != "preserve_image":
                continue
            if region.kind != "composite_visual" and not _is_composite_visual_hint(" ".join(region.notes)):
                continue
            area_ratio = _bbox_area(region.bbox) / 1_000_000
            if area_ratio >= MIN_DEDUP_PROTECTED_AREA:
                continue
            if page_idx not in self.page_render_paths:
                continue
            if page_idx not in self.page_images_map or img_seq >= len(self.page_images_map[page_idx]):
                continue

            render_path = self.page_render_paths[page_idx]
            crop_path = self.page_images_map[page_idx][img_seq]
            try:
                page_img = Image.open(render_path)
                width, height = page_img.size
            except Exception as e:
                region.notes.append(f"small crop expansion skipped: render unavailable ({type(e).__name__})")
                continue

            x1, y1, x2, y2 = region.bbox
            box_w = max(1.0, x2 - x1)
            box_h = max(1.0, y2 - y1)
            new_bbox = [
                max(0.0, x1 - box_w * 0.70),
                max(0.0, y1 - box_h * 0.05),
                min(1000.0, x2 + box_w * 0.08),
                min(1000.0, y2 + max(box_h * 1.70, 260.0)),
            ]
            if _bbox_area(new_bbox) / 1_000_000 < MIN_DEDUP_PROTECTED_AREA:
                region.notes.append("small crop expansion skipped: expanded area still too small")
                continue

            left = int(new_bbox[0] / 1000 * width)
            top = int(new_bbox[1] / 1000 * height)
            right = int(new_bbox[2] / 1000 * width)
            bottom = int(new_bbox[3] / 1000 * height)
            if right - left < MIN_CROP_DIM or bottom - top < MIN_CROP_DIM:
                region.notes.append("small crop expansion skipped: invalid expanded crop")
                continue

            try:
                page_img.crop((left, top, right, bottom)).save(crop_path)
            except Exception as e:
                region.notes.append(f"small crop expansion failed: {type(e).__name__}: {e}")
                continue

            region.bbox = [
                round(new_bbox[0], 2),
                round(new_bbox[1], 2),
                round(new_bbox[2], 2),
                round(new_bbox[3], 2),
            ]
            region.confidence = min(0.86, max(region.confidence, 0.83))
            region.notes.append("expanded small composite crop to include following markdown table")
            expanded += 1

        if expanded:
            print(f"  [Expand] Enlarged {expanded} small composite crop(s) with following table blocks.")

    def _remove_duplicate_tables_after_protected_images(self, refined_text: str) -> str:
        protected_ids = self._protected_image_ids()
        if not protected_ids:
            return refined_text

        image_anchor = re.compile(r"!\[[^\]]*\]\(IMG_P(\d+)_ID(\d+)\)")
        lines = refined_text.splitlines(keepends=True)
        output: list[str] = []
        i = 0
        removed_blocks = 0

        while i < len(lines):
            output.append(lines[i])
            match = image_anchor.search(lines[i])
            if not match or (int(match.group(1)), int(match.group(2))) not in protected_ids:
                i += 1
                continue

            j = i + 1
            blank_buffer: list[str] = []
            while j < len(lines) and not lines[j].strip():
                blank_buffer.append(lines[j])
                j += 1

            if (
                j + 1 < len(lines)
                and self._is_markdown_table_line(lines[j])
                and self._is_markdown_table_separator(lines[j + 1])
            ):
                k = j + 2
                while k < len(lines) and self._is_markdown_table_line(lines[k]):
                    k += 1
                output.append("\n")
                removed_blocks += 1
                region = self._find_region_by_image_id(int(match.group(1)), int(match.group(2)))
                if region is not None:
                    region.notes.append("removed duplicate markdown table after protected image")
                i = k
                continue

            output.extend(blank_buffer)
            i = j

        if removed_blocks:
            print(f"  [Dedup] Removed {removed_blocks} duplicate table block(s) after protected visual images.")
        return "".join(output)

    def _find_region_by_image_id(self, page_idx: int, img_seq: int) -> RegionManifest | None:
        expected_ids = {f"p{page_idx}_vlm_img{img_seq}", f"p{page_idx}_native_crop{img_seq}"}
        if page_idx >= len(self.page_manifests):
            return None
        for region in self.page_manifests[page_idx].regions:
            if region.id in expected_ids:
                return region
        return None

    # ---------------- Phase 3: 锚点回填 (Finalize) ----------------

    def _call_vision_audit(self, image_path, simple_draft_desc):
        """
        Phase 3: 视觉详解 (Quality Priority)
        输入：高清裁剪图 + 简单的初稿(如"Figure")
        输出：详尽的、包含 OCR 和趋势分析的完整描述
        """
        # 这里的 Prompt 就是你要的那个“详尽版”
        user_prompt = (
            "**Task:** Look at this high-resolution figure crop and write a **comprehensive text description**.\n"
            "**Requirements:**\n"
            "1. **Information Equivalence:** The text must allow a reader to understand the image completely without seeing it.\n"
            "2. **Details:** Describe axes, legends, data trends, component relationships, and specific values.\n"
            "3. **OCR:** Transcribe ALL visible text labels exactly.\n"
            "4. **Format:** Output a single cohesive paragraph. No newlines.\n"
            "5. **Start directly:** Do not say 'This image shows'. Start with 'A schematic diagram of...' or 'A line chart showing...'"
        )

        try:
            content = call_vision_api(
                PDF_VISION_AUDIT_PROVIDER, PDF_VISION_AUDIT_MODEL,
                "", user_prompt, image_path
            )
            clean_text = content.replace("\n", " ").replace("\r", "")
            clean_text = clean_text.replace("**", "").replace("*", "")
            return clean_text.strip()
        except Exception as e:
            print(
                f"\n{'='*60}\n"
                f"[LLM ERROR] PDF Phase 3 Audit | provider={PDF_VISION_AUDIT_PROVIDER} model={PDF_VISION_AUDIT_MODEL}\n"
                f"  {type(e).__name__}: {e}\n"
                f"{'='*60}\n",
                file=sys.stderr, flush=True
            )

        return simple_draft_desc

    def _phase3_finalize(self, refined_text):
        """
        Phase 3: 锚点回填与质检
        """
        # 匹配 Phase 1 生成的 ID (不再强制 DESC 前缀)
        img_pattern = re.compile(r'!\[(.*?)\]\((IMG_P(\d+)_ID(\d+))\)')
        
        matches = list(img_pattern.finditer(refined_text))
        audit_results: dict[int, str] = {}

        def audit_one(match_index: int, match):
            draft_desc = match.group(1).strip()
            if draft_desc.startswith("DESC:"):
                draft_desc = draft_desc[5:].strip()
            page_idx = int(match.group(3))
            img_seq = int(match.group(4))
            if not ((page_idx in self.page_images_map) and (img_seq < len(self.page_images_map[page_idx]))):
                return match_index, draft_desc
            img_path = self.page_images_map[page_idx][img_seq]
            print(f"    [Judge] Reviewing P{page_idx}_IMG{img_seq}...")
            final_desc = self._call_vision_audit(img_path, draft_desc)
            return match_index, final_desc.replace("\n", " ").strip()

        if self.enable_audit and matches:
            max_workers = max(1, PDF_VISION_MAX_WORKERS)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(audit_one, i, match): i
                    for i, match in enumerate(matches)
                }
                for future in as_completed(futures):
                    try:
                        match_index, final_desc = future.result()
                        audit_results[match_index] = final_desc
                    except Exception as e:
                        match_index = futures[future]
                        print(
                            f"    [Judge] IMG audit future failed ({type(e).__name__}: {e}); kept draft description",
                            file=sys.stderr,
                            flush=True,
                        )
        
        parts_normal = []
        parts_pure = []
        last_pos = 0
        
        for match_index, match in enumerate(matches):
            start, end = match.span()
            # 这是 Phase 1 写的初稿
            draft_desc = match.group(1).strip()
            # 如果初稿里残留了 DESC: 前缀，去掉它
            if draft_desc.startswith("DESC:"): draft_desc = draft_desc[5:].strip()

            page_idx = int(match.group(3))
            img_seq = int(match.group(4))
            
            parts_normal.append(refined_text[last_pos:start])
            parts_pure.append(refined_text[last_pos:start])
            
            rep_norm = ""
            rep_pure = ""
            
            # 检查图片是否存在
            if (page_idx in self.page_images_map) and (img_seq < len(self.page_images_map[page_idx])):
                img_path = self.page_images_map[page_idx][img_seq]
                rel_path = f"images/{os.path.basename(img_path)}"
                
                final_desc = draft_desc
                
                # === 关键步骤：调用 LLM as Judge ===
                if self.enable_audit:
                    final_desc = audit_results.get(match_index, draft_desc)
                
                # 再次清洗防止换行符破坏 Markdown
                final_desc = final_desc.replace("\n", " ").strip()

                # 1. Normal MD: 标准图片标签
                rep_norm = f"![{final_desc}]({rel_path})"
                
                # 2. Text-Only MD: 引用块增强显示
                # 既然经过了 Judge 精修，描述含金量很高，用引用块展示
                rep_pure = (
                    f"\n> **[Image]** {final_desc}\n"
                    f"> *Source: {rel_path}*\n"
                )
            else:
                rep_norm = f"![{draft_desc} (Missing)]()"
                rep_pure = f"\n> **[Image Missing]** {draft_desc}\n"
            
            parts_normal.append(rep_norm)
            parts_pure.append(rep_pure)
            last_pos = end
            
        parts_normal.append(refined_text[last_pos:])
        parts_pure.append(refined_text[last_pos:])
        
        return "".join(parts_normal), "".join(parts_pure)

    # ---------------- 主流程 ----------------

    def process(self):
        print(f"[*] Starting Scan-Ready PDF Parsing: {self.pdf_path}")
        total_pages = len(self.doc)
        full_draft_buffer = []

        print("\n=== Phase 1: Detect & Crop (Per Page) ===")
        for page_idx in range(total_pages):
            print(f"Processing Page {page_idx + 1}/{total_pages}...")
            page_manifest = self._build_page_manifest(page_idx)
            self.page_manifests.append(page_manifest)
            print(
                f"  [Manifest] page_type={page_manifest.page_type} "
                f"native_chars={page_manifest.native_text_chars} "
                f"image_coverage={page_manifest.image_coverage}"
            )
            
            # 1. 渲染高使用率大图 (用于视觉识别 + 裁剪)
            render_path = self._render_page_as_image(page_idx)
            self.page_render_paths[page_idx] = render_path
            
            if page_manifest.page_type in {"native", "mixed"}:
                final_page_draft = self._native_page_to_markdown(page_idx, page_manifest, render_path)
            else:
                # 2. 视觉识别 (获取含 BBOX 的 Markdown)
                bbox_markdown = self._phase1_vision_scan(render_path, page_idx + 1, page_manifest)
                if _has_untranscribed_placeholder(bbox_markdown):
                    print(f"  [Rescan] Page {page_idx + 1}: handwritten placeholder detected; retrying OCR.")
                    bbox_markdown = self._phase1_handwriting_rescan(
                        render_path, page_idx + 1, bbox_markdown
                    )

                # 3. 解析 BBOX，裁剪图片，生成 ID 标签
                # 这一步会填充 self.page_images_map
                final_page_draft = self._crop_and_replace_bboxes(
                    bbox_markdown, render_path, page_idx, page_manifest
                )
            
            full_draft_buffer.append(final_page_draft)

        joined_draft = "\n\n".join(full_draft_buffer)
        
        print("\n=== Phase 2: Global Structure Refining ===")
        draft_has_placeholder = _has_untranscribed_placeholder(joined_draft)
        refined_text = self._phase2_global_refine(joined_draft)
        refined_has_placeholder = _has_untranscribed_placeholder(refined_text)
        if refined_has_placeholder and not draft_has_placeholder:
            print(
                "  [Warn] Refiner introduced an untranscribed-content placeholder; "
                "using pre-refine OCR draft to preserve handwritten content."
            )
            refined_text = joined_draft
        elif refined_has_placeholder:
            print(
                "  [Warn] Refined text still contains an untranscribed-content placeholder; "
                "handwritten OCR may need review."
            )
        self._expand_small_protected_crops_with_following_tables(refined_text)
        refined_text = self._remove_duplicate_tables_after_protected_images(refined_text)
        
        print("\n=== Phase 3: Final Merge ===")
        final_norm, final_pure = self._phase3_finalize(refined_text)

        base_filename = os.path.splitext(os.path.basename(self.pdf_path))[0]
        suffix = ".partial" if self.had_page_scan_error else ""
        path_norm = os.path.join(self.output_dir, f"{base_filename}{suffix}.md")
        path_pure = os.path.join(self.output_dir, f"{base_filename}_text_only{suffix}.md")
        
        with open(path_norm, 'w', encoding='utf-8') as f: f.write(final_norm)
        with open(path_pure, 'w', encoding='utf-8') as f: f.write(final_pure)
        manifest_path = self._save_manifest(partial=self.had_page_scan_error)
        
        shutil.rmtree(self.temp_dir)
        if self.had_page_scan_error:
            print("[Partial] Page vision scan failed on at least one page; existing complete outputs were not overwritten.")
        print(f"\n[Success] Output: {self.output_dir}")
        print(f"[Success] Manifest: {manifest_path}")

def convert_pdf_to_markdown(input_path, output_dir, enable_audit=True):
    if not os.path.exists(input_path): return
    parser = PDFParser(input_path, output_dir, enable_audit)
    parser.process()
