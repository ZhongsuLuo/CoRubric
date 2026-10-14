from pathlib import Path
import sys
from typing import Optional
from contextlib import asynccontextmanager

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import logging
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import ALLOWED_ORIGINS, CRITERIA_DIR, RESULTS_DIR, CASE_ROOT
from backend.app.schemas import (
    ApiResponse,
    ConvertRequest,
    GradeRequest,
    CriteriaSchema,
    GradingResult,
    CriteriaUpdateRequest,
    CriteriaFileRequest,
    CriteriaCategoryApplyRequest,
    ResultFileRequest,
    CaseReportGradeRequest,
    CaseReportGradeDeleteRequest,
    CaseReportModelGradeAcceptRequest,
    AlignmentEvaluateRequest,
    AlignmentAuditRequest,
    CohortSynthesisRequest,
    AutoGradeRequest,
    AutoGradeBatchCreateRequest,
    AutoGradeBatchCancelRequest,
    AutoGradeBatchConfirmRequest,
    DebugCaseGradeResetRequest,
    DebugExperimentResetRequest,
    ConflictReassessRequest,
    ConflictResolveRequest,
    CriterionAuthorizationRequest,
    CriterionRepairConfirmRequest,
    CriterionProposalConfirmRequest,
    CriterionProposalRejectRequest,
    ImplicitGradeCreateRequest,
    ImplicitGradeUpdateRequest,
)
from backend.app.storage import criteria_list, results_list, delete_json
from backend.app.criteria_lock import criteria_file_lock
from backend.app.criteria_categories import (
    StaleCategoryPreviewError,
    apply_criteria_categories,
    preview_criteria_categories,
)
from backend.app.alignment import (
    alignment_summary,
    evaluate_alignment,
    auto_grade_report,
    accept_model_grade_event,
    run_consistency_audit_for_criterion,
    reassess_conflict,
    resolve_conflict,
    authorize_criterion_batch,
    confirm_criterion_repair,
    cancel_auto_grade_batch,
    create_auto_grade_batch,
    confirm_auto_grade_batch,
    get_auto_grade_batch,
    list_auto_grade_batches,
    ensure_auto_grade_batch_worker,
    resume_criterion_revision_workers,
    advance_criterion_revision,
    clear_case_grading_data,
    CaseGradingResetConflict,
    CorruptAlignmentStore,
    cohort_synthesis,
)
from backend.app.experiment_reset import reset_case_experiment
from backend.app.implicit_grading import (
    StaleProposalError,
    archive_implicit_grade,
    confirm_proposal,
    create_implicit_grade,
    list_criterion_proposals,
    list_implicit_grades,
    reject_proposal,
    retry_implicit_grade,
    run_induction_for_case,
    update_implicit_grade,
)

from backend.app.services import (
    convert_pdf,
    grade_submission,
    update_criteria_with_few_shot,
    save_criteria_file,
    save_result_file,
    load_criteria_file,
    load_result_file,
    list_cases,
    list_case_reports,
    load_case_report,
    save_case_report_grade,
    delete_case_report_grade,
)

logger = logging.getLogger(__name__)

from backend.app.case_imports import router as import_router, recover_imports
from backend.app.case_catalog import rubric_target, bind_rubric


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Resume revision workflows once per process, never as a GET side effect."""
    recover_imports()
    try:
        for case_id in list_cases():
            resume_criterion_revision_workers(case_id)
    except Exception:
        logger.exception("[criterion-revision] startup recovery failed")
    yield


app = FastAPI(
    title="Progressive Grade System API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(import_router)

CASE_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/case-files", StaticFiles(directory=CASE_ROOT), name="case-files")

origins = [origin.strip() for origin in ALLOWED_ORIGINS.split(",")] if ALLOWED_ORIGINS else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error("[validation] path=%s errors=%s body=%s", request.url.path, exc.errors(), exc.body)
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )


def _run_induction_async(case_id: str, criteria_file_name: str | None) -> None:
    def worker() -> None:
        try:
            run_induction_for_case(case_id, criteria_file_name)
        except Exception:
            logger.exception(
                "[implicit-grading] async induction failed case=%s criteria=%s",
                case_id,
                criteria_file_name,
            )

    threading.Thread(
        target=worker,
        name=f"implicit-induction:{case_id}",
        daemon=True,
    ).start()


@app.get("/health", response_model=ApiResponse)
def health_check():
    return ApiResponse(status="ok", data={"service": "grading-backend"})


@app.post("/convert", response_model=ApiResponse)
def convert(req: ConvertRequest):
    try:
        data = convert_pdf(req.pdf_path, req.output_dir, req.enable_audit)
        data["auto_grade_triggered"] = False
        data["auto_grade"] = None
        identity = _verified_conversion_identity(req, data)
        if identity:
            try:
                data["auto_grade"] = auto_grade_report(
                    identity["case_id"],
                    identity["report_name"],
                    identity["criteria_file_name"],
                )
                data["auto_grade_triggered"] = True
            except Exception as exc:
                # Conversion remains successful. Automatic grading fails closed
                # and exposes a structured reason instead of inventing grades.
                logger.exception("[convert] verified auto-grade failed")
                data["auto_grade"] = {"grades": [], "abstentions": [], "failures": [{"reason": str(exc)}]}
        return ApiResponse(status="ok", data=data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _verified_conversion_identity(req: ConvertRequest, data: dict):
    """Return server-verified identity only for a complete in-case conversion."""
    if not req.case_id or not req.report_name:
        return None
    if Path(req.case_id).name != req.case_id or Path(req.report_name).name != req.report_name:
        return None
    case_dir = (Path(CASE_ROOT) / req.case_id).resolve()
    pdf_path = Path(req.pdf_path).resolve()
    output_dir = Path(req.output_dir).resolve()
    expected_pdf = (case_dir / f"{req.report_name}.pdf").resolve()
    expected_output = (case_dir / "markdown" / req.report_name).resolve()
    if pdf_path != expected_pdf or output_dir != expected_output:
        return None
    md_path = Path(data.get("md_path") or "")
    text_path = Path(data.get("text_only_path") or "")
    if not md_path.is_file() or not text_path.is_file() or any(output_dir.glob("*.partial")):
        return None
    if md_path.resolve() != (output_dir / f"{req.report_name}.md").resolve():
        return None
    if text_path.resolve() != (output_dir / f"{req.report_name}_text_only.md").resolve():
        return None
    criteria_name = req.criteria_file_name or "criteria.json"
    if Path(criteria_name).name != criteria_name:
        return None
    shared_criteria = Path(CRITERIA_DIR) / criteria_name
    if not shared_criteria.is_file():
        return None
    return {"case_id": req.case_id, "report_name": req.report_name, "criteria_file_name": criteria_name}


@app.post("/grade", response_model=ApiResponse)
def grade(req: GradeRequest):
    try:
        data = grade_submission(req.submission_md_path, req.criteria_path, req.output_dir)
        return ApiResponse(status="ok", data=data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/criteria/update", response_model=ApiResponse)
def criteria_update(req: CriteriaUpdateRequest):
    try:
        data = update_criteria_with_few_shot(
            req.criteria_path,
            req.criteria_id,
            req.few_shot_example,
            start_revision_workers=True,
        )
        return ApiResponse(status="ok", data=data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/criteria", response_model=ApiResponse)
def list_criteria(case_id: Optional[str] = None):
    items = criteria_list(case_id)
    logger.info(
        "/criteria dir=%s case_id=%s count=%s items=%s",
        CRITERIA_DIR,
        case_id,
        len(items),
        items,
    )
    return ApiResponse(status="ok", data={"items": items})


@app.get("/criteria/{file_name}", response_model=ApiResponse)
def get_criteria(file_name: str):
    try:
        data = load_criteria_file(file_name)
        return ApiResponse(status="ok", data={"item": data})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Criteria file not found")


@app.post("/criteria", response_model=ApiResponse)
def create_criteria(req: CriteriaFileRequest):
    try:
        file_name = rubric_target(req.case_id, req.file_name) if req.case_id else req.file_name
        path = save_criteria_file(
            file_name,
            req.data.model_dump(),
            start_revision_workers=True,
        )
        if req.case_id:
            bind_rubric(req.case_id, file_name)
        return ApiResponse(status="ok", data={"path": path, "file_name": file_name})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/criteria/{file_name}/categories/preview", response_model=ApiResponse)
def preview_categories(file_name: str):
    try:
        return ApiResponse(status="ok", data=preview_criteria_categories(file_name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Criteria file not found")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/criteria/{file_name}/categories/apply", response_model=ApiResponse)
def apply_categories(file_name: str, req: CriteriaCategoryApplyRequest):
    try:
        return ApiResponse(status="ok", data=apply_criteria_categories(file_name, req))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Criteria file not found")
    except StaleCategoryPreviewError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.put("/criteria/{file_name}", response_model=ApiResponse)
def update_criteria(file_name: str, data: CriteriaSchema):
    try:
        path = save_criteria_file(
            file_name,
            data.model_dump(mode="json"),
            start_revision_workers=True,
        )
        return ApiResponse(status="ok", data={"path": path})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/criteria/{file_name}", response_model=ApiResponse)
def delete_criteria(file_name: str):
    with criteria_file_lock(CRITERIA_DIR, file_name):
        delete_json(CRITERIA_DIR, file_name)
    return ApiResponse(status="ok", data={"deleted": file_name})


@app.get("/results", response_model=ApiResponse)
def list_results():
    return ApiResponse(status="ok", data={"items": results_list()})


@app.get("/results/{file_name}", response_model=ApiResponse)
def get_result(file_name: str):
    try:
        data = load_result_file(file_name)
        return ApiResponse(status="ok", data={"item": data})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Result file not found")


@app.post("/results", response_model=ApiResponse)
def create_result(req: ResultFileRequest):
    path = save_result_file(req.file_name, req.data.model_dump())
    return ApiResponse(status="ok", data={"path": path})


@app.put("/results/{file_name}", response_model=ApiResponse)
def update_result(file_name: str, data: GradingResult):
    path = save_result_file(file_name, data)
    return ApiResponse(status="ok", data={"path": path})


@app.delete("/results/{file_name}", response_model=ApiResponse)
def delete_result(file_name: str):
    delete_json(RESULTS_DIR, file_name)
    return ApiResponse(status="ok", data={"deleted": file_name})


@app.get("/cases", response_model=ApiResponse)
def cases():
    return ApiResponse(status="ok", data={"items": list_cases()})




@app.get("/cases/{case_id}/alignment", response_model=ApiResponse)
def case_alignment(case_id: str, criteria_file_name: Optional[str] = None):
    try:
        return ApiResponse(status="ok", data=alignment_summary(case_id, criteria_file_name=criteria_file_name))
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cases/{case_id}/cohort-synthesis", response_model=ApiResponse)
def case_cohort_synthesis(case_id: str, req: CohortSynthesisRequest):
    try:
        return ApiResponse(
            status="ok",
            data=cohort_synthesis(
                case_id,
                criteria_file_name=req.criteria_file_name,
                criterion_id=req.criterion_id,
            ),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Case or criteria file not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("[cohort-synthesis] generation failed")
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/cases/{case_id}/implicit-grades", response_model=ApiResponse)
def case_implicit_grades(case_id: str, criteria_file_name: Optional[str] = None):
    try:
        return ApiResponse(status="ok", data={"items": list_implicit_grades(case_id, criteria_file_name=criteria_file_name)})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/implicit-grades", response_model=ApiResponse)
def case_implicit_grade_create(case_id: str, req: ImplicitGradeCreateRequest):
    try:
        item = create_implicit_grade(case_id, req)
        _run_induction_async(case_id, req.criteria_file_name)
        return ApiResponse(status="ok", data={"item": item})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Case or report not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/cases/{case_id}/implicit-grades/{implicit_grade_id}", response_model=ApiResponse)
def case_implicit_grade_update(case_id: str, implicit_grade_id: str, req: ImplicitGradeUpdateRequest):
    try:
        return ApiResponse(status="ok", data={"item": update_implicit_grade(case_id, implicit_grade_id, req)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Implicit grade not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/implicit-grades/{implicit_grade_id}/retry", response_model=ApiResponse)
def case_implicit_grade_retry(case_id: str, implicit_grade_id: str):
    try:
        item = retry_implicit_grade(case_id, implicit_grade_id)
        _run_induction_async(case_id, item.get("criteria_file_name"))
        return ApiResponse(status="ok", data={"item": item})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Implicit grade not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/cases/{case_id}/implicit-grades/{implicit_grade_id}", response_model=ApiResponse)
def case_implicit_grade_delete(case_id: str, implicit_grade_id: str):
    try:
        return ApiResponse(status="ok", data={"item": archive_implicit_grade(case_id, implicit_grade_id)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Implicit grade not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/cases/{case_id}/criterion-proposals", response_model=ApiResponse)
def case_criterion_proposals(case_id: str, criteria_file_name: Optional[str] = None):
    try:
        return ApiResponse(status="ok", data={"items": list_criterion_proposals(case_id, criteria_file_name=criteria_file_name)})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/criterion-proposals/{proposal_id}/confirm", response_model=ApiResponse)
def case_criterion_proposal_confirm(case_id: str, proposal_id: str, req: CriterionProposalConfirmRequest):
    try:
        return ApiResponse(status="ok", data={"item": confirm_proposal(case_id, proposal_id, req)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except StaleProposalError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/criterion-proposals/{proposal_id}/reject", response_model=ApiResponse)
def case_criterion_proposal_reject(case_id: str, proposal_id: str, req: CriterionProposalRejectRequest):
    try:
        return ApiResponse(status="ok", data={"item": reject_proposal(case_id, proposal_id, req)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except StaleProposalError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/alignment/evaluate", response_model=ApiResponse)
def case_alignment_evaluate(case_id: str, req: AlignmentEvaluateRequest = AlignmentEvaluateRequest()):
    try:
        return ApiResponse(status="ok", data=evaluate_alignment(case_id, req.criteria_id, req.criteria_file_name))
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/alignment/audit", response_model=ApiResponse)
def case_alignment_audit(case_id: str, req: AlignmentAuditRequest = AlignmentAuditRequest()):
    try:
        summary = alignment_summary(case_id, criteria_file_name=req.criteria_file_name)
        pending_by_id = {
            str(job.get("criterion_id")): job
            for job in summary.get("audit_jobs", [])
            if job.get("status") == "pending" and job.get("criterion_id")
        }
        ids = [str(item) for item in (req.criteria_ids or [])]
        if req.criteria_id:
            ids.append(str(req.criteria_id))
        if not ids:
            ids = sorted(pending_by_id)
        results = [
            run_consistency_audit_for_criterion(
                case_id,
                req.criteria_file_name or pending_by_id.get(str(criterion_id), {}).get("criteria_file_name"),
                criterion_id,
            )
            for criterion_id in dict.fromkeys(ids)
        ]
        for result in results:
            job = result.get("job") or {}
            if job.get("status") != "done":
                continue
            evaluate_alignment(
                case_id,
                str(job.get("criterion_id") or job.get("criteria_id")),
                job.get("criteria_file_name") or req.criteria_file_name,
            )
            advance_criterion_revision(
                case_id,
                str(job.get("criteria_file_name") or req.criteria_file_name or "criteria.json"),
                str(job.get("criterion_id") or job.get("criteria_id")),
            )
        return ApiResponse(status="ok", data={"items": results, "alignment": alignment_summary(case_id, criteria_file_name=req.criteria_file_name)})
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/cases/{case_id}/alignment/conflicts/{conflict_id}/reassess",
    response_model=ApiResponse,
    deprecated=True,
)
def case_conflict_reassess(case_id: str, conflict_id: str, req: ConflictReassessRequest):
    try:
        item = reassess_conflict(case_id, conflict_id, req.teacher_feedback)
        criteria_file_name = (item.get("conflict") or {}).get("criteria_file_name")
        return ApiResponse(status="ok", data={**item, "alignment": alignment_summary(case_id, criteria_file_name=criteria_file_name)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Conflict or event not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/alignment/conflicts/{conflict_id}/resolve", response_model=ApiResponse)
def case_conflict_resolve(case_id: str, conflict_id: str, req: ConflictResolveRequest):
    try:
        item = resolve_conflict(
            case_id,
            conflict_id,
            req.selected_pattern_id,
            req.teacher_reason,
        )
        criteria_file_name = (item.get("conflict") or {}).get("criteria_file_name")
        return ApiResponse(status="ok", data={**item, "alignment": alignment_summary(case_id, criteria_file_name=criteria_file_name)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Conflict not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/alignment/criteria/{criterion_id}/authorize", response_model=ApiResponse)
def case_criterion_authorize(case_id: str, criterion_id: str, req: CriterionAuthorizationRequest):
    try:
        authorization = authorize_criterion_batch(
            case_id,
            req.criteria_file_name,
            criterion_id,
            req.authorized_by,
        )
        return ApiResponse(status="ok", data={"authorization": authorization, "alignment": alignment_summary(case_id, criteria_file_name=req.criteria_file_name)})
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/alignment/criteria/{criterion_id}/repair/confirm", response_model=ApiResponse)
def case_criterion_repair_confirm(case_id: str, criterion_id: str, req: CriterionRepairConfirmRequest):
    try:
        repair = confirm_criterion_repair(
            case_id,
            req.criteria_file_name,
            criterion_id,
            req.confirmed_by,
        )
        return ApiResponse(status="ok", data={**repair, "alignment": alignment_summary(case_id, criteria_file_name=req.criteria_file_name)})
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/auto-grade-batches", response_model=ApiResponse)
def case_auto_grade_batch_create(case_id: str, req: AutoGradeBatchCreateRequest):
    try:
        batch = create_auto_grade_batch(
            case_id,
            req.criteria_file_name,
            req.created_by,
            report_names=req.report_names,
            start_report_name=req.start_report_name,
        )
        if batch.get("status") == "queued":
            ensure_auto_grade_batch_worker(case_id, str(batch["batch_id"]))
        return ApiResponse(status="ok", data={"batch": batch})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Case or converted reports not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/debug/clear-grades", response_model=ApiResponse)
def case_debug_clear_grades(case_id: str, req: DebugCaseGradeResetRequest):
    """Debug-only endpoint; never removes source reports or rubric files."""
    try:
        cleared = clear_case_grading_data(case_id, req.confirm_case_id)
        return ApiResponse(status="ok", data={"case_id": case_id, "cleared": cleared})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Case not found")
    except CaseGradingResetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/debug/reset-experiment", response_model=ApiResponse)
def case_debug_reset_experiment(case_id: str, req: DebugExperimentResetRequest):
    """Debug-only reset to the configured immutable rubric baseline."""
    try:
        return ApiResponse(
            status="ok",
            data=reset_case_experiment(
                case_id,
                req.confirm_case_id,
                req.criteria_file_name,
            ),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Case or experiment rubric baseline not found")
    except CaseGradingResetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/cases/{case_id}/auto-grade-batches", response_model=ApiResponse)
def case_auto_grade_batches(case_id: str, criteria_file_name: Optional[str] = None):
    try:
        batches = list_auto_grade_batches(case_id, criteria_file_name)
        for batch in batches:
            if batch.get("status") in {"queued", "running"}:
                ensure_auto_grade_batch_worker(case_id, str(batch["batch_id"]))
        return ApiResponse(status="ok", data={"items": batches})
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cases/{case_id}/auto-grade-batches/{batch_id}", response_model=ApiResponse)
def case_auto_grade_batch(case_id: str, batch_id: str):
    try:
        batch = get_auto_grade_batch(case_id, batch_id)
        if batch.get("status") in {"queued", "running"}:
            ensure_auto_grade_batch_worker(case_id, batch_id)
        return ApiResponse(status="ok", data={"batch": batch})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Auto-grade batch not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cases/{case_id}/auto-grade-batches/{batch_id}/confirm", response_model=ApiResponse)
def case_auto_grade_batch_confirm(
    case_id: str,
    batch_id: str,
    req: AutoGradeBatchConfirmRequest = AutoGradeBatchConfirmRequest(),
):
    try:
        batch = confirm_auto_grade_batch(case_id, batch_id, req.confirmed_by)
        ensure_auto_grade_batch_worker(case_id, batch_id)
        return ApiResponse(status="ok", data={"batch": batch})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Auto-grade batch not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cases/{case_id}/auto-grade-batches/{batch_id}/cancel", response_model=ApiResponse)
def case_auto_grade_batch_cancel(
    case_id: str,
    batch_id: str,
    req: AutoGradeBatchCancelRequest = AutoGradeBatchCancelRequest(),
):
    try:
        batch = cancel_auto_grade_batch(case_id, batch_id, req.cancelled_by)
        return ApiResponse(status="ok", data={"batch": batch})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Auto-grade batch not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cases/{case_id}/reports/{report_name}/auto-grade", response_model=ApiResponse)
def case_report_auto_grade(case_id: str, report_name: str, req: AutoGradeRequest = AutoGradeRequest()):
    try:
        return ApiResponse(
            status="ok",
            data=auto_grade_report(
                case_id,
                report_name,
                req.criteria_file_name,
                criteria_ids=req.criteria_ids,
                scheduling_reason=req.reason,
            ),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Case or report not found")
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/cases/{case_id}/reports", response_model=ApiResponse)
def case_reports(case_id: str):
    data = list_case_reports(case_id)
    return ApiResponse(status="ok", data={"items": data})


@app.get("/cases/{case_id}/reports/{report_name}", response_model=ApiResponse)
def case_report(case_id: str, report_name: str, criteria_file_name: Optional[str] = None):
    try:
        data = load_case_report(case_id, report_name, criteria_file_name)
        return ApiResponse(status="ok", data=data)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Report not found")


@app.post("/cases/{case_id}/reports/{report_name}/grade", response_model=ApiResponse)
def case_report_grade(case_id: str, report_name: str, req: CaseReportGradeRequest):
    try:
        logger.info(
            "[grade] incoming case=%s report=%s payload=%s",
            case_id,
            report_name,
            req.model_dump(),
        )
        data = save_case_report_grade(case_id, report_name, req)
        return ApiResponse(status="ok", data={"result": data})
    except FileNotFoundError as e:
        logger.error("[grade] path missing: %s", e)
        raise HTTPException(status_code=404, detail="Case or report not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("[grade] unexpected error case=%s report=%s", case_id, report_name)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cases/{case_id}/reports/{report_name}/grade/accept-model", response_model=ApiResponse)
def case_report_model_grade_accept(case_id: str, report_name: str, req: CaseReportModelGradeAcceptRequest):
    try:
        logger.info(
            "[grade-accept-model] incoming case=%s report=%s payload=%s",
            case_id,
            report_name,
            req.model_dump(),
        )
        data = accept_model_grade_event(case_id, report_name, req)
        return ApiResponse(
            status="ok",
            data={
                "result": data["result"],
                "event": data["event"],
                "accepted": data["accepted"],
                "duplicate": data["duplicate"],
            },
        )
    except FileNotFoundError as e:
        logger.error("[grade-accept-model] path missing: %s", e)
        raise HTTPException(status_code=404, detail=str(e) or "Case, report, or model grade not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except CorruptAlignmentStore as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("[grade-accept-model] unexpected error case=%s report=%s", case_id, report_name)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/cases/{case_id}/reports/{report_name}/grade", response_model=ApiResponse)
def case_report_grade_delete(case_id: str, report_name: str, req: CaseReportGradeDeleteRequest):
    try:
        logger.info("[grade-delete] incoming case=%s report=%s payload=%s", case_id, report_name, req.model_dump())
        data = delete_case_report_grade(case_id, report_name, req)
        return ApiResponse(status="ok", data={"result": data})
    except FileNotFoundError as e:
        logger.error("[grade-delete] path missing: %s", e)
        raise HTTPException(status_code=404, detail="Case or report not found")
    except Exception as e:
        logger.exception("[grade-delete] unexpected error case=%s report=%s", case_id, report_name)
        raise HTTPException(status_code=500, detail=str(e))


# Fallback POST endpoint for environments blocking DELETE with body
@app.post("/cases/{case_id}/reports/{report_name}/grade/delete", response_model=ApiResponse)
def case_report_grade_delete_post(case_id: str, report_name: str, req: CaseReportGradeDeleteRequest):
    try:
        logger.info("[grade-delete-post] incoming case=%s report=%s payload=%s", case_id, report_name, req.model_dump())
        data = delete_case_report_grade(case_id, report_name, req)
        return ApiResponse(status="ok", data={"result": data})
    except FileNotFoundError as e:
        logger.error("[grade-delete-post] path missing: %s", e)
        raise HTTPException(status_code=404, detail="Case or report not found")
    except Exception as e:
        logger.exception("[grade-delete-post] unexpected error case=%s report=%s", case_id, report_name)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
