"""Three Alibaba parsing configurations plus a local diagnostic reference."""
import base64
import json
import math
import re
import time
from contextlib import nullcontext
from urllib.parse import urlparse

import pymupdf as fitz
import httpx

from backend.ocr.common import IMAGE_RE, write_json


class ProviderError(RuntimeError):
    def __init__(self, body):
        super().__init__("ProviderBusinessError")
        self.code = body.get("Code")
        self.message = body.get("Message")


def error_info(error, secrets=()):
    # Retain actionable provider diagnostics, but never headers or signed URLs.
    response = getattr(error, "response", None)
    result = {"error_type": type(error).__name__, "http_status": getattr(response, "status_code", None)}
    details = {"code": getattr(error, "code", None), "message": getattr(error, "message", None)}
    if isinstance(error, httpx.HTTPStatusError):
        try:
            body = response.json()
            details = body.get("error", body)
        except (ValueError, AttributeError):
            details = {}
    if isinstance(details, dict):
        for key in ("code", "message"):
            value = details.get(key)
            if value is None:
                continue
            value = str(value)
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"https?://[^\s<>]+", "[URL REDACTED]", value)
            result[f"provider_{key}"] = value[:1000]
    return result


def token_cost(usage, pricing, prefix):
    input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
    rates = [pricing[f"{prefix}_input_per_million_cny"], pricing[f"{prefix}_output_per_million_cny"]]
    if input_tokens is None or output_tokens is None or any(rate < 0 for rate in rates):
        return None
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


def vision_request(settings, image_bytes, target, pricing, prefix):
    started = time.perf_counter()
    record = {"status": "failed", "api_requests": 1, "usage": {}, "estimated_cny": None}
    try:
        payload = {"model": settings["model"], "max_tokens": settings["max_tokens"],
                   "messages": [{"role": "user", "content": [
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_bytes).decode()}},
                       {"type": "text", "text": settings["prompt"]}]}]}
        if "enable_thinking" in settings:
            payload["enable_thinking"] = settings["enable_thinking"]
        if "response_format" in settings:
            payload["response_format"] = settings["response_format"]
        # No SDK auto-retries: a failed request may still have incurred a charge.
        with httpx.Client(timeout=settings["timeout_seconds"]) as client:
            response = client.post(settings["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings["api_key"]},
                json=payload)
            response.raise_for_status()
            body = response.json()
        write_json(target.with_suffix(".response.json"), body)
        choice = body["choices"][0]
        text = choice["message"].get("content") or ""
        if not isinstance(text, str):
            raise ValueError("UnexpectedContentType")
        target.write_text(text, encoding="utf-8")
        record["usage"] = body.get("usage") or {}
        record["estimated_cny"] = token_cost(record["usage"], pricing, prefix)
        record["finish_reason"] = choice.get("finish_reason")
        record["status"] = "complete" if text.strip() and choice.get("finish_reason") == "stop" else "partial"
    except Exception as error:
        record.update(error_info(error, [settings["api_key"]]))
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
            try:
                delay = float(error.response.headers.get("Retry-After", "1"))
                if math.isfinite(delay):
                    record["retry_after_seconds"] = max(0, min(delay, 60))
            except ValueError:
                pass  # Date-form Retry-After falls back to the bounded default delay.
    record["wall_seconds"] = round(time.perf_counter() - started, 3)
    write_json(target.with_suffix(".metrics.json"), record)
    return record


def download(url, timeout):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host.endswith((".aliyuncs.com", ".alicdn.com")):
        raise ValueError("UnexpectedArtifactHost")
    # Separate unauthenticated request; never forward the API credential to a file URL.
    response = httpx.get(url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    return response.content


def localize_images(markdown, directory, timeout):
    cache, warnings = {}, []

    def replace(match):
        alt, url = match.groups()
        if url in cache:
            return f"![{alt}]({cache[url]})"
        try:
            content = download(url, timeout)
            # Decode/re-encode as PNG, independent of the URL's extension.
            pix = fitz.Pixmap(content)
            if pix.colorspace is None:
                raise ValueError("InvalidImage")
            if pix.colorspace.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            relative = f"images/figure_{len(cache) + 1:03d}.png"
            (directory / "images").mkdir(exist_ok=True)
            pix.save(directory / relative)
            cache[url] = relative
            return f"![{alt}]({relative})"
        except Exception as error:
            warnings.append({"image_download": error_info(error)})
            return match.group(0)

    return IMAGE_RE.sub(replace, markdown), warnings


def docmind_client(settings):
    from alibabacloud_docmind_api20220711.client import Client
    from alibabacloud_tea_openapi.utils_models import Config
    return Client(Config(access_key_id=settings["access_key_id"], access_key_secret=settings["access_key_secret"],
                         security_token=settings["security_token"] or None,
                         type="sts" if settings["security_token"] else "access_key",
                         endpoint=settings["endpoint"], region_id=settings["region_id"]))


def docmind_data(response):
    body = response.body.to_map()
    if body.get("Code") not in (None, "200", 200, "Success", "success"):
        raise ProviderError(body)
    data = body["Data"]
    return json.loads(data) if isinstance(data, str) else data


def parse_docmind(pdf, directory, config, enhanced, resume=False, page_count=None, image_lock=None):
    from alibabacloud_docmind_api20220711 import models
    from darabonba.runtime import RuntimeOptions
    settings = config["docmind"]
    client = docmind_client(settings)
    runtime = RuntimeOptions(autoretry=False, read_timeout=settings["request_timeout_seconds"] * 1000,
                             connect_timeout=10000)
    started = time.perf_counter()
    job_file = directory / "job.json"
    calls = 0
    if resume:
        if not job_file.exists():
            raise ValueError("NoSavedJobId_DoNotResubmitUnknownJob")
        job_id = json.loads(job_file.read_text())["id"]
    else:
        write_json(directory / "submit_started.json", {"started_at_unix": time.time()})
        with open(pdf, "rb") as stream:
            request = models.SubmitDocParserJobAdvanceRequest(
                file_url_object=stream, file_name="source.pdf", formula_enhancement=settings["formula_enhancement"],
                llm_enhancement=enhanced, enhancement_mode="VLM" if enhanced else None,
                output_format=["markdown", "visualLayoutInfo"])
            calls += 1
            job_id = docmind_data(client.submit_doc_parser_job_advance(request, runtime))["Id"]
        write_json(job_file, {"id": job_id, "submitted_at_unix": time.time()})
    while True:
        calls += 1
        status = docmind_data(client.query_doc_parser_status_with_options(models.QueryDocParserStatusRequest(id=job_id), runtime))
        write_json(directory / "status.json", status)
        state = str(status["Status"]).lower()
        if state == "success":
            break
        if state not in {"init", "processing"}:
            raise RuntimeError("DocMindJobFailedOrUnknownState")
        if time.perf_counter() - started >= settings["max_wait_seconds"]:
            raise TimeoutError("DocMindPollingTimeout_ResumeSavedJob")
        time.sleep(settings["poll_seconds"])
    links = status.get("OutputFormatResult", [])
    markdown_url = next((item["OutputFileUrl"] for item in links if item.get("OutputType") == "markdown"), None)
    if not markdown_url:
        raise ValueError("DocMindMissingMarkdownURL")
    raw = download(markdown_url, settings["request_timeout_seconds"]).decode("utf-8-sig")
    (directory / "raw.md").write_text(raw, encoding="utf-8")
    layouts = []
    total = status["NumberOfSuccessfulParsing"]
    batch = settings["layout_batch_size"]
    for offset in range(0, total, batch):
        calls += 1
        result = docmind_data(client.get_doc_parser_result_with_options(
            models.GetDocParserResultRequest(id=job_id, layout_num=offset, layout_step_size=batch), runtime))
        write_json(directory / f"layouts_{offset:05d}.json", result)
        chunk = result["layouts"]
        if not chunk:
            raise ValueError("MissingLayoutBatch")
        layouts.extend(chunk)
    write_json(directory / "layouts.json", layouts)
    if len(layouts) != total:
        raise ValueError("IncompleteLayouts")
    with image_lock if image_lock is not None else nullcontext():
        markdown, warnings = localize_images(raw, directory, settings["request_timeout_seconds"])
    (directory / "output.md").write_text(markdown, encoding="utf-8")
    if page_count is None:
        with fitz.open(pdf) as doc:
            page_count = len(doc)
    price = config["pricing"]["docmind_vlm_per_page_cny" if enhanced else "docmind_basic_per_page_cny"]
    return {"status": "complete" if markdown.strip() and not warnings else "partial", "warnings": warnings,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "timing_scope": "resume_query_and_download" if resume else "submit_through_download",
            "logical_sdk_calls": calls, "job_id": job_id, "layout_count": len(layouts),
            "estimated_cny": page_count * price if price >= 0 else None,
            "estimated_cost_scope": "whole_job_including_original_submission",
            "provider_character_count_not_billed_tokens": status.get("Tokens")}


