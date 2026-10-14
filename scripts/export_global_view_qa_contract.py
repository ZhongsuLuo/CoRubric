from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from backend.app import alignment, implicit_grading, main, services
from scripts.seed_qa_business_case import (
    QA_CASE_ID,
    QA_CRITERIA_FILE,
    QA_LARGE_CASE_ID,
    QA_READY_CASE_ID,
    QA_SCALE_CASES,
    seed_qa_case,
)


def _persistence_hash(case_root: Path, criteria_dir: Path) -> str:
    digest = hashlib.sha256()
    for namespace, root in (("cases", case_root), ("criteria", criteria_dir)):
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            digest.update(f"{namespace}/{path.relative_to(root).as_posix()}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def export_contract(output: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="grading-global-simulation-") as temp_dir:
        root = Path(temp_dir)
        case_root = root / "case"
        criteria_dir = root / "criteria"
        manifest = seed_qa_case(case_root, criteria_dir)
        for module in (alignment, services, main, implicit_grading):
            module.CASE_ROOT = case_root
            module.CRITERIA_DIR = criteria_dir

        persistence_hash_before = _persistence_hash(case_root, criteria_dir)
        client = TestClient(main.app)
        case_ids = [
            QA_CASE_ID,
            QA_READY_CASE_ID,
            *[QA_SCALE_CASES[count] for count in sorted(QA_SCALE_CASES)],
            QA_LARGE_CASE_ID,
        ]
        responses = {}
        for case_id in case_ids:
            response = client.get(
                f"/cases/{case_id}/alignment",
                params={"criteria_file_name": QA_CRITERIA_FILE},
            )
            response.raise_for_status()
            responses[case_id] = response.json()["data"]

        previous_qa_mode = os.environ.get("GLOBAL_SYNTHESIS_QA")
        os.environ["GLOBAL_SYNTHESIS_QA"] = "1"
        try:
            synthesis_response = client.post(
                f"/cases/{QA_READY_CASE_ID}/cohort-synthesis",
                json={"criteria_file_name": QA_CRITERIA_FILE},
            )
            synthesis_response.raise_for_status()
            synthesis = synthesis_response.json()["data"]
        finally:
            if previous_qa_mode is None:
                os.environ.pop("GLOBAL_SYNTHESIS_QA", None)
            else:
                os.environ["GLOBAL_SYNTHESIS_QA"] = previous_qa_mode
        persistence_hash_after = _persistence_hash(case_root, criteria_dir)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "manifest": manifest,
                    "criteria": json.loads(
                        (criteria_dir / QA_CRITERIA_FILE).read_text(encoding="utf-8")
                    ),
                    "responses": responses,
                    "synthesis": synthesis,
                    "persistence_hash_before": persistence_hash_before,
                    "persistence_hash_after": persistence_hash_after,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return output


def main_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Export deterministic backend responses for Global View frontend simulation."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export_contract(args.output))


if __name__ == "__main__":
    main_cli()
