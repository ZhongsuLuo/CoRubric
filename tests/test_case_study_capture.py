import hashlib
import json

import httpx
import pytest

from scripts.case_study_capture import install_recorder


def test_recorder_preserves_provider_response_without_saving_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    install_recorder(tmp_path)
    body = b'{"id":"real-response-shape","choices":[{"message":{"content":"ok"}}]}'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    with httpx.Client(transport=transport) as client:
        response = client.post(
            "https://provider.invalid/v1/chat/completions",
            headers={"Authorization": "Bearer secret-not-for-cache"},
            json={"model": "test", "messages": [{"role": "user", "content": "input"}]},
        )
    assert response.content == body
    files = list((tmp_path / "raw_llm").iterdir())
    assert len(files) == 3
    assert all(b"secret-not-for-cache" not in path.read_bytes() for path in files)
    assert next(path for path in files if path.name.endswith(".response.json")).read_bytes() == body
    metadata = json.loads(next(path for path in files if path.name.endswith(".meta.json")).read_text())
    assert metadata["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert metadata["status_code"] == 200


def test_recorder_keeps_failure_without_inventing_response(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    install_recorder(tmp_path)

    def fail(request):
        raise httpx.ConnectError("connection failed", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.ConnectError):
            client.post("https://provider.invalid/v1/chat/completions", json={"model": "test"})
    assert not list((tmp_path / "raw_llm").glob("*.response.json"))
    metadata = json.loads(next((tmp_path / "raw_llm").glob("*.meta.json")).read_text())
    assert metadata["error"] == "ConnectError: connection failed"


def test_recorder_does_not_capture_local_api_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    install_recorder(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))) as client:
        client.get("http://localhost:8000/cases")
    assert not list((tmp_path / "raw_llm").iterdir())
