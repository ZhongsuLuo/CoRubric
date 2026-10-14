"""CPU-only region planning and conservative assembly; never calls a model."""
import math

import pymupdf as fitz


def page_sizes(status):
    return {p["PageIdCurDoc"]: (p["ImageWidth"], p["ImageHeight"])
            for item in status.get("OutputFormatResult", []) if item.get("OutputType") == "pageImage"
            for p in item.get("Pages", [])}


def rect_from_layout(block, page, dimensions):
    width, height = dimensions
    points = block.get("pos", [])
    if width <= 0 or height <= 0 or len(points) < 2:
        raise ValueError("MissingLayoutCoordinates")
    values = [(p["x"] / width * page.rect.width, p["y"] / height * page.rect.height) for p in points]
    if not all(math.isfinite(v) for point in values for v in point):
        raise ValueError("InvalidLayoutCoordinates")
    rect = fitz.Rect(min(x for x, _ in values), min(y for _, y in values),
                     max(x for x, _ in values), max(y for _, y in values))
    if rect.is_empty or (rect & page.rect).get_area() < rect.get_area() * .95:
        raise ValueError("OutOfPageLayoutCoordinates")
    return rect & page.rect


def render_region(doc, block, path, dpi, max_pixels):
    page = doc[block["page"] - 1]
    clip = fitz.Rect(block["bbox"]) + (-3, -3, 3, 3)
    clip &= page.rect
    scale = min(dpi / 72, math.sqrt(max_pixels / clip.get_area()))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    pix.save(path)
    return {"width": pix.width, "height": pix.height, "render_bbox": list(clip)}


