"""Single-process deployment: authenticated UI and same-origin /api."""
import base64
import binascii
import os
import posixpath
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import CASE_ROOT, CRITERIA_DIR, PROJECT_ROOT, RESULTS_DIR


def create_app(api_app=None):
    username = os.getenv("GRADING_AUTH_USER", "").strip()
    password = os.getenv("GRADING_AUTH_PASSWORD", "")
    if not username or len(password) < 16 or ":" in username:
        raise RuntimeError("Set GRADING_AUTH_USER and GRADING_AUTH_PASSWORD (at least 16 characters)")
    frontend = Path(os.getenv("GRADING_FRONTEND_DIR", str(PROJECT_ROOT / "frontend/dist")))
    if not (frontend / "index.html").is_file():
        raise RuntimeError("Frontend build missing; run npm ci && npm run build in frontend/")
    for directory in (CASE_ROOT, CRITERIA_DIR, RESULTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    if api_app is None:
        from .main import app as api_app

    @asynccontextmanager
    async def lifespan(_app):
        # Mounted apps do not receive lifespan automatically.
        async with api_app.router.lifespan_context(api_app):
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    expected = f"{username}:{password}".encode("utf-8")

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = posixpath.normpath("/" + request.url.path.lstrip("/"))
        if path == "/api/health" and request.method in {"GET", "HEAD"}:
            return JSONResponse({"status": "ok", "data": {"service": "grading-backend"}})
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            credentials = base64.b64decode(token, validate=True)
        except (ValueError, binascii.Error):
            credentials = b""
        if scheme.lower() != "basic" or not secrets.compare_digest(credentials, expected):
            return JSONResponse({"detail": "Authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Grading", charset="UTF-8"'})
        # Local filesystem tools and experiment resets are never published.
        if ("/debug/" in path + "/" or path in {
            "/api/convert", "/api/grade", "/api/criteria/update",
            "/api/docs", "/api/redoc", "/api/openapi.json", "/api/docs/oauth2-redirect",
        }):
            return JSONResponse({"detail": "Not available in production"}, status_code=404)
        if path.startswith("/api/case-files/") and Path(path).suffix.lower() not in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf",
        }:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site write rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/api", api_app)
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
