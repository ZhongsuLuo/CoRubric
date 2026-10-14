from typing import Dict, Any, List
import os
import json
import re
from pathlib import Path
from urllib.parse import quote

from .schemas import (
    FewShotExample,
    CaseReportGradeRequest,
    CaseReportGradeDeleteRequest,
    CriteriaSchema,
    GradingResult,
    validate_structured_score_budgets,
)
from .config import CRITERIA_DIR, RESULTS_DIR, CASE_ROOT, VISIBLE_CASE_IDS
from .criteria_lock import criteria_file_lock
from .case_catalog import imported_cases
from .criteria_versions import grading_criterion_payload, record_saved_versions
from .grading.common import natural_name_key
from .storage import read_json, write_json
from .alignment import (
    append_teacher_grade_event,
    append_teacher_delete_event,
    enqueue_consistency_audits_for_criteria_file,
    ensure_criterion_revision_workers_for_jobs,
    materialize_projection,
)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _changed_criteria_ids(before: Dict[str, Any] | None, after: Dict[str, Any] | None) -> List[str]:
    before_by_id = {str(item.get("criteria_id")): item for item in (before or {}).get("criteria", []) if item.get("criteria_id") is not None}
    after_by_id = {str(item.get("criteria_id")): item for item in (after or {}).get("criteria", []) if item.get("criteria_id") is not None}
    changed = []
    for criteria_id, item in after_by_id.items():
        before_item = before_by_id.get(criteria_id)
        if before_item is None or grading_criterion_payload(before_item) != grading_criterion_payload(item):
            changed.append(criteria_id)
    return changed


def convert_pdf(pdf_path: str, output_dir: str, enable_audit: bool) -> Dict[str, Any]:
    from backend.libs.qwen_pdf_parser import convert_pdf_to_markdown

    convert_pdf_to_markdown(pdf_path, output_dir, enable_audit=enable_audit)

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    md_path = os.path.join(output_dir, f"{base_name}.md")
    text_only_path = os.path.join(output_dir, f"{base_name}_text_only.md")

    return {
        "output_dir": output_dir,
        "md_path": md_path if os.path.exists(md_path) else None,
        "text_only_path": text_only_path if os.path.exists(text_only_path) else None,
    }


def grade_submission(submission_md_path: str, criteria_path: str, output_dir: str) -> Dict[str, Any]:
    from backend.libs.grade import grade_submission as grade_runner

    grade_runner(submission_md_path, criteria_path, output_dir)

    base_name = os.path.splitext(os.path.basename(submission_md_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_grade.json")
    result = _load_json(output_path) if os.path.exists(output_path) else None

    return {
        "output_path": output_path,
        "result": result,
    }


def update_criteria_with_few_shot(
    criteria_path: str,
    criteria_id: str,
    example: FewShotExample,
    *,
    start_revision_workers: bool = False,
) -> Dict[str, Any]:
    from backend.libs.update_criteria import optimize_rubric_with_examples

    path = Path(criteria_path)
    with criteria_file_lock(path.parent, path.name):
        criteria_data = CriteriaSchema.model_validate(_load_json(criteria_path)).model_dump(mode="json")
        before_data = json.loads(json.dumps(criteria_data))
        updated = False
        before_desc = None

        for item in criteria_data.get("criteria", []):
            if item.get("criteria_id") == criteria_id:
                before_desc = item.get("description")
                item.setdefault("few_shot_examples", []).append(example.model_dump())
                updated = True
                break

        if not updated:
            return {
                "updated": False,
                "message": "criteria_id not found",
            }

        with open(criteria_path, "w", encoding="utf-8") as f:
            json.dump(criteria_data, f, ensure_ascii=False, indent=2)

        optimized_path = optimize_rubric_with_examples(criteria_path, output_path=criteria_path)

        after_data = _load_json(optimized_path) if optimized_path else criteria_data
        try:
            after_data = CriteriaSchema.model_validate(after_data).model_dump(mode="json")
            validate_structured_score_budgets(after_data)
        except ValueError:
            # The optimizer writes in place. Never leave an invalid structured
            # rubric on disk when its proposed allocation exceeds/is below the
            # criterion score budget.
            with open(criteria_path, "w", encoding="utf-8") as f:
                json.dump(before_data, f, ensure_ascii=False, indent=2)
            raise
        after_desc = None
        before_criterion = None
        after_criterion = None
        for item in before_data.get("criteria", []):
            if item.get("criteria_id") == criteria_id:
                before_criterion = item
                break
        for item in after_data.get("criteria", []):
            if item.get("criteria_id") == criteria_id:
                after_desc = item.get("description")
                after_criterion = item
                break

        before_scoring_points = (before_criterion or {}).get("scoring_points") or []
        after_scoring_points = (after_criterion or {}).get("scoring_points") or []
        before_scoring_aggregation = (before_criterion or {}).get("scoring_aggregation")
        after_scoring_aggregation = (after_criterion or {}).get("scoring_aggregation")
        description_changed = before_desc is not None and after_desc is not None and before_desc != after_desc
        structured_changed = (
            before_scoring_points != after_scoring_points
            or before_scoring_aggregation != after_scoring_aggregation
        )
        changed_ids = _changed_criteria_ids(before_data, after_data)
        audit_jobs = enqueue_consistency_audits_for_criteria_file(path.name, changed_ids, "criteria_update")
        if start_revision_workers:
            ensure_criterion_revision_workers_for_jobs(audit_jobs)

        return {
            "updated": True,
            "description_changed": description_changed,
            "structured_changed": structured_changed,
            "before": before_desc,
            "after": after_desc,
            "before_criterion": before_criterion,
            "after_criterion": after_criterion,
            "before_scoring_points": before_scoring_points,
            "after_scoring_points": after_scoring_points,
            "before_scoring_aggregation": before_scoring_aggregation,
            "after_scoring_aggregation": after_scoring_aggregation,
            "criteria": after_data,
            "audit_jobs": audit_jobs,
        }


def save_criteria_file(
    file_name: str,
    data: Dict[str, Any],
    *,
    start_revision_workers: bool = False,
) -> str:
    data = CriteriaSchema.model_validate(data).model_dump(mode="json")
    validate_structured_score_budgets(data)
    with criteria_file_lock(CRITERIA_DIR, file_name):
        path = Path(CRITERIA_DIR) / file_name
        before = (
            CriteriaSchema.model_validate(_load_json(str(path))).model_dump(mode="json")
            if path.exists()
            else None
        )
        written = write_json(CRITERIA_DIR, file_name, data)
        record_saved_versions(CRITERIA_DIR, before, data)
    audit_jobs = enqueue_consistency_audits_for_criteria_file(
        file_name,
        _changed_criteria_ids(before, data),
        "criteria_file_saved",
    )
    if start_revision_workers:
        ensure_criterion_revision_workers_for_jobs(audit_jobs)
    return written


def save_result_file(file_name: str, data: GradingResult) -> str:
    return write_json(RESULTS_DIR, file_name, data.model_dump(mode="json"))


def load_criteria_file(file_name: str) -> Dict[str, Any]:
    return CriteriaSchema.model_validate(read_json(CRITERIA_DIR, file_name)).model_dump(mode="json")


def load_result_file(file_name: str) -> Dict[str, Any]:
    return GradingResult.model_validate(read_json(RESULTS_DIR, file_name)).model_dump(mode="json")


def list_cases() -> List[str]:
    if not os.path.exists(CASE_ROOT):
        return []
    configured = [
        case_id
        for case_id in VISIBLE_CASE_IDS
        if os.path.isdir(os.path.join(CASE_ROOT, case_id))
    ]
    return list(dict.fromkeys([*configured, *imported_cases(CASE_ROOT)]))


def list_case_reports(case_id: str) -> List[Dict[str, Any]]:
    markdown_root = os.path.join(CASE_ROOT, case_id, "markdown")
    grade_root = os.path.join(CASE_ROOT, case_id, "grade_result")
    if not os.path.exists(markdown_root):
        return []
    reports = []
    for name in sorted(os.listdir(markdown_root), key=natural_name_key):
        if name.startswith('.'):
            continue
        report_dir = os.path.join(markdown_root, name)
        if not os.path.isdir(report_dir):
            continue
        md_files = [f for f in os.listdir(report_dir) if f.endswith(".md") and not f.endswith("_text_only.md")]
        md_file = md_files[0] if md_files else None
        new_result_path = os.path.join(grade_root, f"{name}_grade.json")
        result_path = new_result_path if os.path.exists(new_result_path) else None
        reports.append(
            {
                "name": name,
                "markdown_path": os.path.join(report_dir, md_file) if md_file else None,
                "result_path": result_path,
            }
        )
    return reports


def load_case_report(case_id: str, report_name: str, criteria_file_name: str | None = None) -> Dict[str, Any]:
    report_dir = os.path.join(CASE_ROOT, case_id, "markdown", report_name)
    grade_dir = os.path.join(CASE_ROOT, case_id, "grade_result")
    if not os.path.exists(report_dir):
        raise FileNotFoundError(report_dir)
    md_files = [f for f in os.listdir(report_dir) if f.endswith(".md") and not f.endswith("_text_only.md")]
    md_file = md_files[0] if md_files else None
    md_path = os.path.join(report_dir, md_file) if md_file else None
    markdown = None
    if md_path and os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            markdown = f.read()

    if markdown:
        def replace_src(match):
            alt = match.group(1)
            src = match.group(2)
            tail = match.group(3)
            if src.startswith("http://") or src.startswith("https://") or src.startswith("/case-files/"):
                return match.group(0)
            normalized = src.lstrip("./")
            relative = f"{case_id}/markdown/{report_name}/{normalized}"
            encoded = quote(relative)
            new_src = f"/case-files/{encoded}"
            return f"![{alt}]({new_src}{tail}"

        markdown = re.sub(r"!\[(.*?)\]\(([^)\s]+)(\))", replace_src, markdown)

    if criteria_file_name:
        result = materialize_projection(case_id, report_name, criteria_file_name=criteria_file_name)
    else:
        new_result_path = os.path.join(grade_dir, f"{report_name}_grade.json")
        result = _load_json(new_result_path) if os.path.exists(new_result_path) else None
    if result:
        for item in result.get("criteria", []):
            if "criteria_id" in item:
                item["criteria_id"] = str(item["criteria_id"])
            item.pop("anchor_occurrence", None)
            item.setdefault("graded_by", "user")
    return {
        "name": report_name,
        "markdown": markdown,
        "result": result,
    }


def save_case_report_grade(case_id: str, report_name: str, payload: CaseReportGradeRequest) -> Dict[str, Any]:
    """Persist one server-authored teacher event and materialize its projection."""
    return append_teacher_grade_event(case_id, report_name, payload)["result"]


def delete_case_report_grade(case_id: str, report_name: str, payload: CaseReportGradeDeleteRequest) -> Dict[str, Any]:
    """Append a server-authored deletion event without erasing grade history."""
    return append_teacher_delete_event(case_id, report_name, payload)["result"]
