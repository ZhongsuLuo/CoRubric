"""Record real API actions and complete UI read snapshots for case demos."""
from __future__ import annotations

import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/case_studies/2026-09-05"
CRITERIA = {
    "case 1": "Case 1 评分标准.json",
    "case 2": "“原子吸收光谱法测定饮用水中的镁含量”实验评分标准_criterion_categories_v2.json",
}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def request(method: str, path: str, payload=None):
    record_id = f"{time.time_ns()}_{uuid.uuid4().hex[:8]}"
    record = {"method": method, "path": path, "payload": payload, "started_at": time.time()}
    try:
        response = httpx.request(method, "http://127.0.0.1:8000" + path, json=payload, timeout=600)
        record.update(status_code=response.status_code, response=response.json())
        response.raise_for_status()
        return record["response"]
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        record["elapsed_seconds"] = time.time() - record["started_at"]
        write_json(RUN / "api" / f"{record_id}.json", record)


def checkpoint(stage: str, case: str, reviews: list[str]) -> dict:
    if len(reviews) < 3:
        raise ValueError("Each stage needs three explicit review conclusions")
    destination = RUN / "stages" / stage
    if destination.exists():
        raise FileExistsError(destination)
    criteria = CRITERIA[case]
    query = f"?criteria_file_name={criteria}"
    paths = [
        "/health", "/cases", f"/criteria?case_id={case}", f"/criteria/{criteria}",
        f"/cases/{case}/reports", f"/cases/{case}/alignment{query}",
        f"/cases/{case}/implicit-grades{query}",
        f"/cases/{case}/criterion-proposals{query}",
        f"/cases/{case}/auto-grade-batches{query}",
    ]
    names = [p.name for p in (RUN / "working/case" / case / "markdown").iterdir() if p.is_dir()]
    paths.extend(f"/cases/{case}/reports/{name}{query}" for name in sorted(names))
    responses = {path: request("GET", path) for path in paths}
    write_json(destination / "reads.json", responses)
    write_json(destination / "stage.json", {
        "stage": stage, "case": case, "recorded_at": time.time(), "reviews": reviews,
        "raw_llm_response_count": len(list((RUN / "raw_llm").glob("*.response.json"))),
    })
    source = RUN / "working/case" / case
    for name in ["alignment", "grade_result", "implicit_grades.json", "criterion_proposals.json"]:
        path = source / name
        if path.is_dir():
            shutil.copytree(path, destination / "state" / name)
        elif path.is_file():
            (destination / "state").mkdir(exist_ok=True)
            shutil.copy2(path, destination / "state" / name)
    shutil.copy2(RUN / "working/criteria" / criteria, destination / "rubric.json")
    return responses[f"/cases/{case}/alignment{query}"]["data"]


if __name__ == "__main__":
    command, *arguments = sys.argv[1:]
    if command == "execute":
        for operation in json.loads(Path(arguments[0]).read_text()):
            if "checkpoint" in operation:
                result = checkpoint(operation["checkpoint"], operation["case"], operation["reviews"])
            else:
                result = request(operation["method"], operation["path"], operation.get("payload"))
            if operation.get("save_as"):
                write_json(RUN / operation["save_as"], result)
            print(operation.get("checkpoint") or operation["path"], "recorded", flush=True)
    elif command == "checkpoint":
        stage, case, review_file = arguments
        result = checkpoint(stage, case, json.loads(Path(review_file).read_text()))
    else:
        path = arguments[0]
        payload = json.loads(Path(arguments[1]).read_text()) if len(arguments) > 1 else None
        result = request(command.upper(), path, payload)
    write_json(RUN / "last_response.json", result)
    print("Full response saved to", RUN / "last_response.json")
