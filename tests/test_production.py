from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import production


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    monkeypatch.setenv("GRADING_AUTH_USER", "acceptance")
    monkeypatch.setenv("GRADING_AUTH_PASSWORD", "acceptance-test-only-password")
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>Production UI</html>")
    monkeypatch.setenv("GRADING_FRONTEND_DIR", str(frontend))
    for name in ("CASE_ROOT", "CRITERIA_DIR", "RESULTS_DIR"):
        monkeypatch.setattr(production, name, tmp_path / name)
    calls = []

    @asynccontextmanager
    async def lifespan(_app):
        calls.append("start")
        yield
        calls.append("stop")

    api = FastAPI(lifespan=lifespan)

    @api.api_route("/cases", methods=["GET", "POST"])
    def cases():
        return {"items": []}

    return production.create_app(api), calls


def test_production_requires_credentials(monkeypatch):
    monkeypatch.delenv("GRADING_AUTH_USER", raising=False)
    monkeypatch.delenv("GRADING_AUTH_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="GRADING_AUTH_USER"):
        production.create_app()


def test_authentication_assets_health_and_lifespan(deployed):
    app, calls = deployed
    with TestClient(app) as client:
        assert calls == ["start"]
        assert client.get("/api/health").status_code == 200
        for path in ("/", "/api/cases", "/api/case-files/report.pdf"):
            assert client.get(path).status_code == 401
            assert client.get(path, auth=("acceptance", "wrong")).status_code == 401
        assert client.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401
        client.auth = ("acceptance", "acceptance-test-only-password")
        assert "Production UI" in client.get("/").text
        response = client.get("/api/cases")
        assert response.json() == {"items": []}
        assert response.headers["cache-control"] == "no-store"
    assert calls == ["start", "stop"]


@pytest.mark.parametrize("path", [
    "/api/convert", "/api/grade", "/api/criteria/update/",
    "/api/cases/demo/debug/reset-experiment", "/api/cases/demo/debug/clear-grades/",
    "/api/docs", "/api/openapi.json", "/api/case-files/demo/alignment/events.json",
    "/api/case-files/demo/attack.html", "/api/case-files/demo/attack.svg",
])
def test_local_tools_and_private_files_not_published(deployed, path):
    app, _ = deployed
    with TestClient(app) as client:
        assert client.post(path, auth=("acceptance", "acceptance-test-only-password")).status_code == 404


def test_cross_site_writes_are_blocked(deployed):
    app, _ = deployed
    with TestClient(app) as client:
        client.auth = ("acceptance", "acceptance-test-only-password")
        assert client.post("/api/cases", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/cases", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/api/cases", headers={"Origin": "http://testserver"}).status_code == 200
