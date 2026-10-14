"""Run the unchanged backend with isolated case data and raw LLM recording.

Usage: .venv/bin/python scripts/case_study_capture.py RUN_DIRECTORY
Only /chat/completions response bodies are recorded; credentials are excluded.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def configure(run: Path) -> None:
    for variable, relative in {
        "GRADING_CASE_ROOT": "working/case",
        "GRADING_CRITERIA_DIR": "working/criteria",
        "GRADING_DATA_DIR": "working/api",
        "GRADING_CRITERIA_BASELINE_DIR": "working/criteria_baselines",
        "GRADING_CASE_BASELINE_DIR": "working/case_baselines",
    }.items():
        os.environ[variable] = str(run / relative)
    os.environ.pop("GLOBAL_SYNTHESIS_QA", None)


def install_recorder(run: Path) -> None:
    original_send = httpx.Client.send
    directory = run / "raw_llm"
    directory.mkdir(parents=True, exist_ok=True)

    def recorded_send(client, request, *args, **kwargs):
        if not request.url.path.endswith("/chat/completions"):
            return original_send(client, request, *args, **kwargs)
        record_id = f"{time.time_ns()}_{uuid.uuid4().hex[:8]}"
        request_body = request.read()
        started = time.time()
        (directory / f"{record_id}.request.json").write_bytes(request_body)
        metadata = {
            "id": record_id,
            "started_at_unix": started,
            "endpoint": str(request.url.copy_with(query=None)),
            "request_sha256": hashlib.sha256(request_body).hexdigest(),
        }
        try:
            response = original_send(client, request, *args, **kwargs)
            body = response.read()
            (directory / f"{record_id}.response.json").write_bytes(body)
            metadata.update(
                status_code=response.status_code,
                response_sha256=hashlib.sha256(body).hexdigest(),
                provider_request_id=response.headers.get("x-request-id"),
            )
            return response
        except Exception as error:
            metadata["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            metadata["elapsed_seconds"] = time.time() - started
            (directory / f"{record_id}.meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
            )

    httpx.Client.send = recorded_send


if __name__ == "__main__":
    run_directory = Path(sys.argv[1]).resolve()
    configure(run_directory)
    install_recorder(run_directory)
    sys.path.insert(0, str(ROOT))
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000)
