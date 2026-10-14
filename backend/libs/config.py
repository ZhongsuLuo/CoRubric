"""
LLM configuration.
Grading and alignment tasks share one provider + model configuration.
API keys are mapped by provider.
"""
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Load .env
_BACKEND_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    _env = _BACKEND_DIR / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════
# API Keys — one per provider
# ═══════════════════════════════════════════════════════════
BAILIAN_API_KEY = os.getenv("BAILIAN_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")
BAILIAN_BASE_URL = os.getenv("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Set dashscope SDK key if available
if BAILIAN_API_KEY:
    try:
        import dashscope
        dashscope.api_key = BAILIAN_API_KEY
    except ImportError:
        pass

# ═══════════════════════════════════════════════════════════
# Human-readable chat model configuration
# ═══════════════════════════════════════════════════════════

def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class ChatModelConfig:
    provider: str
    model: str
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    json_mode: bool = False

    def request_options(self) -> dict[str, Any]:
        """Return provider-specific OpenAI SDK request options."""
        provider = self.provider.strip().lower()
        options: dict[str, Any] = {}
        extra_body: dict[str, Any] = {}
        if self.thinking is not None:
            if provider in {"bailian", "dashscope", "qwen"}:
                extra_body["enable_thinking"] = self.thinking
            elif provider == "deepseek":
                extra_body["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
        if self.reasoning_effort and self.thinking is not False:
            options["reasoning_effort"] = self.reasoning_effort
        if self.json_mode:
            options["response_format"] = {"type": "json_object"}
        if extra_body:
            options["extra_body"] = extra_body
        return options


# Teacher-aligned scoring model. Keep thinking disabled for lower latency and
# predictable JSON output across grading, alignment, and cohort synthesis.
GRADING_MODEL_CONFIG = ChatModelConfig(
    provider=os.getenv("GRADING_LLM_PROVIDER", os.getenv("GRADING_PROVIDER", "bailian")),
    model=os.getenv("GRADING_LLM_MODEL", os.getenv("GRADING_MODEL", "qwen3.8-max")),
    thinking=_env_bool("GRADING_LLM_THINKING", False),
    json_mode=_env_bool("GRADING_LLM_JSON_MODE", True),
)

# Remaining task configs use the legacy (provider, model) tuple shape.

# PDF Phase 1 — Vision detection & OCR
PDF_VISION_SCAN = (
    os.getenv("PDF_VISION_SCAN_PROVIDER", "dashscope"),
    os.getenv("PDF_VISION_SCAN_MODEL", "5.4nano"),
)

# PDF Phase 2 — Text restructuring
PDF_TEXT_REFINE = (
    os.getenv("PDF_TEXT_REFINE_PROVIDER", "dashscope"),
    os.getenv("PDF_TEXT_REFINE_MODEL", "5.5"),
)

# PDF Phase 3 — Image audit / detailed description
PDF_VISION_AUDIT = (
    os.getenv("PDF_VISION_AUDIT_PROVIDER", "dashscope"),
    os.getenv("PDF_VISION_AUDIT_MODEL", "5.4nano"),
)

# Criteria optimization (few-shot → rewrite description)
CRITERIA_OPTIMIZE = (
    os.getenv("CRITERIA_OPTIMIZE_PROVIDER", "dashscope"),
    os.getenv("CRITERIA_OPTIMIZE_MODEL", "5.5"),
)

# Criteria transformation (docx/txt → JSON)
CRITERIA_TRANSFORM = (
    os.getenv("CRITERIA_TRANSFORM_PROVIDER", "dashscope"),
    os.getenv("CRITERIA_TRANSFORM_MODEL", "5.5"),
)

# PDF Figure verification — check crop completeness & purity (multi-image)
PDF_FIGURE_VERIFY = (
    os.getenv("PDF_FIGURE_VERIFY_PROVIDER", "dashscope"),
    os.getenv("PDF_FIGURE_VERIFY_MODEL", "5.4nano"),
)

# Conservative concurrency limits for remote PDF/VLM work.
# Keep these small by default because accuracy and API stability matter more than raw speed.
PDF_PAGE_MAX_WORKERS = int(os.getenv("PDF_PAGE_MAX_WORKERS", "1"))
PDF_VISION_MAX_WORKERS = int(os.getenv("PDF_VISION_MAX_WORKERS", "3"))
PDF_API_RETRIES = int(os.getenv("PDF_API_RETRIES", "3"))
PDF_API_RETRY_BASE_SECONDS = float(os.getenv("PDF_API_RETRY_BASE_SECONDS", "1.5"))

# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _call_with_retries(label: str, fn):
    attempts = max(1, PDF_API_RETRIES)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt >= attempts:
                break
            sleep_s = PDF_API_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(
                f"[Retry] {label} failed on attempt {attempt}/{attempts}: "
                f"{type(e).__name__}: {e}; retrying in {sleep_s:.1f}s",
                flush=True,
            )
            time.sleep(sleep_s)
    raise last_error

def get_chat_client(provider: str):
    """Return an OpenAI-compatible client for the given provider."""
    from openai import OpenAI

    normalized = provider.strip().lower()
    if normalized in {"bailian", "dashscope", "qwen"}:
        if not BAILIAN_API_KEY:
            raise RuntimeError("Missing BAILIAN_API_KEY in backend/.env")
        return OpenAI(
            api_key=BAILIAN_API_KEY,
            base_url=BAILIAN_BASE_URL,
        )
    if normalized == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("Missing DEEPSEEK_API_KEY in backend/.env")
        return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    if normalized == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError("Missing OPENAI_API_KEY in backend/.env")
        return OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")


def call_vision_api(provider: str, model: str, system_prompt: str,
                    user_prompt: str, image_path: str) -> str:
    """Call a vision API with image + text. Works for both dashscope and openai."""
    if provider == "dashscope":
        from dashscope import MultiModalConversation
        from http import HTTPStatus

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"image": f"file://{image_path}"},
                {"text": user_prompt}
            ]},
        ]
        res = _call_with_retries(
            f"DashScope vision model={model}",
            lambda: MultiModalConversation.call(model=model, messages=messages),
        )
        if res.status_code == HTTPStatus.OK:
            return res.output.choices[0].message.content[0]["text"]
        raise RuntimeError(
            f"DashScope vision error: status={res.status_code} "
            f"message={res.message}"
        )
    else:
        import base64

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        client = get_chat_client(provider)
        resp = _call_with_retries(
            f"OpenAI-compatible vision model={model}",
            lambda: client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{b64}"
                        }},
                    ]},
                ],
            ),
        )
        return resp.choices[0].message.content


def call_vision_api_multi(provider: str, model: str, system_prompt: str,
                          user_prompt: str, image_paths: list[str]) -> str:
    """Call a vision API with MULTIPLE images. Works for both dashscope and openai."""
    if provider == "dashscope":
        from dashscope import MultiModalConversation
        from http import HTTPStatus

        image_blocks = [{"image": f"file://{p}"} for p in image_paths]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [*image_blocks, {"text": user_prompt}]},
        ]
        res = _call_with_retries(
            f"DashScope multi-vision model={model}",
            lambda: MultiModalConversation.call(model=model, messages=messages),
        )
        if res.status_code == HTTPStatus.OK:
            return res.output.choices[0].message.content[0]["text"]
        raise RuntimeError(
            f"DashScope vision error: status={res.status_code} "
            f"message={res.message}"
        )
    else:
        import base64

        image_contents = []
        for p in image_paths:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            image_contents.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

        client = get_chat_client(provider)
        resp = _call_with_retries(
            f"OpenAI-compatible multi-vision model={model}",
            lambda: client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [*image_contents, {"type": "text", "text": user_prompt}]},
                ],
            ),
        )
        return resp.choices[0].message.content
