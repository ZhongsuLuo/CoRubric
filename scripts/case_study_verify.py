"""Verify a frozen real-response bundle and write its integrity report."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def anchor_is_grounded(anchor: dict, source: str) -> bool:
    if anchor["anchor_type"] == "text":
        return bool(anchor.get("text")) and source.count(anchor["text"]) == 1
    if anchor["anchor_type"] == "absence":
        location = anchor.get("expected_location", "")
        return bool(location and anchor.get("missing_item")) and source.count(location) == 1
    return False


def verify(run: Path) -> dict:
    errors = []
    sources = json.loads((run / "source_manifest.json").read_text())
    changed_sources = [name for name, expected in sources.items() if digest(ROOT / name) != expected]
    if changed_sources:
        errors.append("Business sources changed: " + ", ".join(changed_sources))
    raw = []
    for path in sorted((run / "raw_llm").glob("*.meta.json")):
        item = json.loads(path.read_text())
        for kind in ["request", "response"]:
            key = kind + "_sha256"
            if key in item and digest(path.with_name(path.name.replace(".meta.", f".{kind}."))) != item[key]:
                errors.append("Raw hash mismatch: " + path.name)
        raw.append(item)
    cases = {}
    reads = {}
    for number in [1, 2]:
        case = f"case {number}"
        folder = run / "stages" / f"c{number}_final"
        responses = json.loads((folder / "reads.json").read_text())
        reads[case] = responses
        alignment = next(value["data"] for key, value in responses.items() if "/alignment?" in key)
        rows = alignment["global_report_summaries"]
        rubric = json.loads((folder / "rubric.json").read_text())
        missing = {row["report_name"]: [cid for cid, item in row["criteria_scores"].items()
                                      if item["status"] != "graded"] for row in rows}
        missing = {name: ids for name, ids in missing.items() if ids}
        cases[case] = {
            "reports": len(rows), "criteria": len(rubric["criteria"]),
            "scored_cells": sum(row["graded_criteria_count"] for row in rows),
            "expected_cells": len(rows) * len(rubric["criteria"]), "missing": missing,
            "global_readiness": alignment["global_readiness"],
            "scores": {row["report_name"]: row["criteria_scores"] for row in rows},
        }
        if missing or len(rows) != 14 or len(rubric["criteria"]) != {1: 5, 2: 15}[number]:
            errors.append(case + " incomplete report matrix")
    teacher_checks = []
    for item in json.loads((run / "teacher_plan.json").read_text()):
        folder = run / "working/case" / item["case"] / "markdown" / item["report"]
        source = next(path for path in folder.glob("*.md") if not path.stem.endswith("_text_only"))
        text = source.read_text()
        anchors = [item["anchor"], *item.get("supporting_anchors", [])]
        response = next(value["data"] for key, value in reads[item["case"]].items()
                        if f"/reports/{item['report']}?" in key)
        grade = next((grade for grade in response["result"]["criteria"]
                      if grade["criteria_id"] == item["criterion_id"]), {})
        saved_anchors = grade.get("evidence_anchors", [])
        anchors_preserved = all(
            any(anchor.items() <= saved.items() for saved in saved_anchors)
            for anchor in anchors
        )
        check = {
            "id": item["id"], "source_hash_matches": digest(source) == item["source_sha256"],
            "anchors_grounded": all(anchor_is_grounded(anchor, text) for anchor in anchors),
            "score_matches": grade.get("criteria_score") == item["score"],
            "teacher_source": grade.get("graded_by") == "user",
            "anchors_preserved": anchors_preserved,
            "has_three_reviews": len(item["reviews"]) >= 3,
        }
        if not all(value for key, value in check.items() if key != "id"):
            errors.append("Teacher evidence check failed: " + item["id"])
        teacher_checks.append(check)
    stage_files = sorted((run / "stages").glob("*/stage.json"))
    stages = [json.loads(path.read_text()) for path in stage_files]
    if any(len(stage["reviews"]) < 3 for stage in stages):
        errors.append("Stage missing three reviews")
    report = {
        "passed": not errors, "errors": errors, "business_source_files_checked": len(sources),
        "changed_business_sources": changed_sources, "raw_response_count": sum("response_sha256" in item for item in raw),
        "raw_http_statuses": dict(Counter(str(item.get("status_code", "transport_error")) for item in raw)),
        "raw_requests_without_response": len(list((run / "raw_llm").glob("*.request.json")))
            - len(list((run / "raw_llm").glob("*.response.json"))),
        "stages_with_three_reviews": len(stages), "teacher_checks": teacher_checks, "cases": cases,
    }
    (run / "integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    journal = ["# 实测逐阶段复核", "", "每个逻辑阶段三次复核。按实际记录时间排序；失败和恢复阶段均保留。", ""]
    for stage in sorted(stages, key=lambda item: item["recorded_at"]):
        journal.extend(["## " + stage["stage"], "", *[f"{i}. {review}" for i, review in enumerate(stage["reviews"], 1)], ""])
    (run / "JOURNAL.md").write_text("\n".join(journal))
    return report


if __name__ == "__main__":
    result = verify(Path(sys.argv[1]).resolve())
    print(json.dumps({key: value for key, value in result.items() if key not in {"cases", "teacher_checks"}}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
