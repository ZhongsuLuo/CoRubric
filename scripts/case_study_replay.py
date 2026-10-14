"""Serve captured case stages with the original frontend, without an LLM client.

Build frontend first, then: .venv/bin/python scripts/case_study_replay.py RUN_DIRECTORY
Open http://127.0.0.1:8001/demo. Unknown requests fail closed.
"""
from __future__ import annotations

import html
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

def request_key(path: str) -> str:
    parts = urlsplit(path)
    query = urlencode(sorted(parse_qsl(parts.query)))
    return unquote(parts.path) + ("?" + query if query else "")


def load_stages(run: Path) -> dict:
    stages = {}
    for folder in sorted((run / "stages").iterdir()):
        if not (folder / "reads.json").exists():
            continue
        metadata = json.loads((folder / "stage.json").read_text())
        stages[folder.name] = {
            **metadata,
            "reads": {request_key(k): v for k, v in json.loads((folder / "reads.json").read_text()).items()},
            "synthesis": {p.stem.removeprefix("synthesis_"): json.loads(p.read_text())
                          for p in folder.glob("synthesis_*.json")},
        }
    return stages


def create_app(run: Path, frontend: Path | None = None) -> FastAPI:
    app = FastAPI(title="Recorded case study replay")
    stages = load_stages(run)
    selected = {}
    for name, stage in stages.items():
        selected.setdefault(stage["case"], name)
    app.state.selected = selected
    app.state.stages = stages
    app.state.misses = []
    front = frontend or run / "frontend_dist"

    @app.get("/demo/status")
    async def status():
        return {"mode": "recorded-only", "selected": selected, "cache_misses": app.state.misses,
                "llm_client_loaded": False}

    @app.post("/demo/select/{stage_name}")
    async def select(stage_name: str):
        if stage_name not in stages:
            return JSONResponse({"error": "Unknown recorded stage"}, status_code=404)
        selected[stages[stage_name]["case"]] = stage_name
        return RedirectResponse("/demo?stage=" + stage_name, status_code=303)

    @app.get("/demo", response_class=HTMLResponse)
    async def demo(stage: str | None = None):
        name = stage if stage in stages else selected["case 1"]
        current = stages[name]
        options = "".join(f'<option value="{key}" {"selected" if key == name else ""}>{key}</option>'
                          for key in stages)
        reviews = "".join(f"<li>{html.escape(text)}</li>" for text in current["reviews"])
        names = list(stages)
        next_name = names[min(names.index(name) + 1, len(names) - 1)]
        return f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>真实实测 Case 回放</title>
<style>body{{margin:0;font:14px system-ui;background:#f6f8fb;color:#16302b}}header{{padding:14px 22px;display:flex;gap:18px;align-items:center}}select,button{{padding:8px}}details{{margin:0 22px 12px}}iframe{{width:100%;height:calc(100vh - 170px);border:0;background:white}}a{{color:#17675c}}</style>
<header><b>真实实测回放 · 不调用模型</b><form method="post" id="stage-form" action="/demo/select/{name}">
<select onchange="this.form.action='/demo/select/'+this.value;this.form.submit()">{options}</select></form>
<form method="post" action="/demo/select/{next_name}"><button>下一步骤</button></form>
<span>在下方系统选择 {html.escape(current['case'])}</span><a href="/demo/status" target="_blank">回放状态</a></header>
<details open><summary>本步骤三次复核 · 当前为保存时的真实状态</summary><ol>{reviews}</ol></details>
<iframe src="/" title="原系统界面"></iframe>
</html>'''

    app.mount("/api/case-files", StaticFiles(directory=run / "working/case"), name="case-files")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def cached(request: Request, path: str):
        route = "/" + path
        payload = await request.json() if request.method != "GET" and await request.body() else {}
        case = next((case for case in selected if route.startswith(f"/cases/{case}/")), None)
        query = dict(request.query_params)
        if case is None:
            case = query.get("case_id")
        if case is None and route.startswith("/criteria/"):
            case = next((case for case, name in selected.items()
                         if request_key(route) in stages[name]["reads"]), None)
        selected_stage = selected.get(case, selected["case 1"])
        snapshot = stages[selected_stage]
        key = request_key(route + ("?" + str(request.query_params) if query else ""))
        result = snapshot["reads"].get(key) if request.method == "GET" else None
        if request.method == "POST" and route.endswith("/cohort-synthesis"):
            result = snapshot["synthesis"].get(str(payload.get("criterion_id") or "all"))
        if result is not None:
            return JSONResponse(result, headers={"X-Case-Replay": snapshot["stage"]})
        miss = {"method": request.method, "path": key, "stage": snapshot["stage"], "time": time.time()}
        app.state.misses.append(miss)
        with (run / "replay_access.jsonl").open("a") as stream:
            stream.write(json.dumps(miss, ensure_ascii=False) + "\n")
        return JSONResponse({"status": "cache_miss", "message": "该操作未录制。请用回放工具栏切换真实步骤；不会调用模型。"}, status_code=409)

    if front.exists():
        app.mount("/", StaticFiles(directory=front, html=True), name="frontend")

    return app


if __name__ == "__main__":
    uvicorn.run(create_app(Path(sys.argv[1]).resolve()), host="127.0.0.1", port=8001)
