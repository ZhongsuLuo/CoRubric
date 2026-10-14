FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.10.6 AS uv
FROM python:3.11-slim-bookworm AS runtime
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/ ./backend/
COPY --from=frontend /src/frontend/dist ./frontend/dist
RUN useradd --uid 10001 --create-home grading && mkdir -p /app/backend/data && chown -R grading:grading /app/backend/data
USER grading
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["/app/.venv/bin/uvicorn", "backend.app.production:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
