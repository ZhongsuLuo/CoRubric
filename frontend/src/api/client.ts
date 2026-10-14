import type {
  AlignmentState,
  ApiResponse,
  AutoGradeResponse,
  AutoGradeBatch,
  CriteriaItem,
  CriteriaCategoryPreview,
  CriteriaSchema,
  CriterionProposalItem,
  EvidenceAnchor,
  GradingResult,
  ImplicitGradeItem,
  ScoringPointScore,
  CohortSynthesis,
} from '../types/schema'
import type { CaseImportJob } from '../types/imports'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const REQUEST_TIMEOUT_MS = 45000
const AUDIT_REQUEST_TIMEOUT_MS = 180000

type ApiRequestInit = RequestInit & { timeoutMs?: number }

const request = async <T>(path: string, options?: ApiRequestInit): Promise<T> => {
  const controller = new AbortController()
  const { timeoutMs, ...fetchOptions } = options || {}
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs ?? REQUEST_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...fetchOptions,
      signal: fetchOptions.signal || controller.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('Request timed out. Please try again.')
    }
    throw err
  } finally {
    window.clearTimeout(timeout)
  }
  if (!res.ok) {
    throw new Error(await res.text())
  }
  return res.json() as Promise<T>
}

export const api = {
  uploadCaseFolder: (name: string, files: File[], engine: 'hybrid' | 'legacy') => {
    const body = new FormData()
    body.append('name', name)
    body.append('engine', engine)
    body.append('paths', JSON.stringify(files.map((file) => file.webkitRelativePath || file.name)))
    files.forEach((file) => body.append('files', file, file.name))
    return request<ApiResponse<CaseImportJob>>('/case-imports', {
      method: 'POST', body, headers: {}, timeoutMs: 600000,
    })
  },
  listCaseImports: () => request<ApiResponse<{ items: CaseImportJob[] }>>('/case-imports'),
  retryCaseImport: (id: string) => request<ApiResponse<CaseImportJob>>(`/case-imports/${id}/retry`, { method: 'POST' }),
  health: () => request<ApiResponse<{ service: string }>>('/health'),
  convert: (payload: {
    pdf_path: string
    output_dir: string
    enable_audit: boolean
    case_id?: string
    report_name?: string
    criteria_file_name?: string
  }) =>
    request<
      ApiResponse<{
        output_dir: string
        md_path: string | null
        text_only_path: string | null
        auto_grade_triggered?: boolean
        auto_grade?: AutoGradeResponse | null
      }>
    >('/convert', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  grade: (payload: { submission_md_path: string; criteria_path: string; output_dir: string }) =>
    request<ApiResponse<{ output_path: string; result: GradingResult | null }>>('/grade', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  criteriaUpdate: (payload: {
    criteria_path: string
    criteria_id: string
    few_shot_example: {
      example_input: string
      example_output_description: string
      example_output_score: number
    }
  }) =>
    request<
      ApiResponse<{
        updated: boolean
        description_changed?: boolean
        structured_changed?: boolean
        before?: string
        after?: string
        before_criterion?: CriteriaItem | null
        after_criterion?: CriteriaItem | null
        before_scoring_points?: CriteriaItem['scoring_points']
        after_scoring_points?: CriteriaItem['scoring_points']
        before_scoring_aggregation?: CriteriaItem['scoring_aggregation']
        after_scoring_aggregation?: CriteriaItem['scoring_aggregation']
        criteria?: CriteriaSchema
        audit_jobs?: unknown[]
      }>
    >('/criteria/update', { method: 'POST', body: JSON.stringify(payload) }),
  listCriteria: (caseId?: string) =>
    request<ApiResponse<{ items: string[] }>>(
      `/criteria${caseId ? `?case_id=${encodeURIComponent(caseId)}` : ''}`,
    ),
  getCriteria: (fileName: string) =>
    request<ApiResponse<{ item: CriteriaSchema }>>(`/criteria/${encodeURIComponent(fileName)}`),
  saveCriteria: (fileName: string, data: CriteriaSchema, caseId?: string) =>
    request<ApiResponse<{ path: string; file_name?: string }>>('/criteria', {
      method: 'POST',
      body: JSON.stringify({ file_name: fileName, data, ...(caseId ? { case_id: caseId } : {}) }),
    }),
  previewCriteriaCategories: (fileName: string) =>
    request<ApiResponse<CriteriaCategoryPreview>>(
      `/criteria/${encodeURIComponent(fileName)}/categories/preview`,
      { method: 'POST', timeoutMs: AUDIT_REQUEST_TIMEOUT_MS },
    ),
  applyCriteriaCategories: (
    fileName: string,
    payload: {
      source_content_hash: string
      assignments: Array<{ criteria_id: string; section: string }>
    },
  ) =>
    request<
      ApiResponse<{
        item: CriteriaSchema
        categories: Array<{ section: string; count: number }>
        content_hash: string
      }>
    >(`/criteria/${encodeURIComponent(fileName)}/categories/apply`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteCriteria: (fileName: string) =>
    request<ApiResponse<{ deleted: string }>>(`/criteria/${encodeURIComponent(fileName)}`, {
      method: 'DELETE',
    }),
  listResults: () => request<ApiResponse<{ items: string[] }>>('/results'),
  getResult: (fileName: string) =>
    request<ApiResponse<{ item: GradingResult }>>(`/results/${fileName}`),
  saveResult: (fileName: string, data: GradingResult) =>
    request<ApiResponse<{ path: string }>>('/results', {
      method: 'POST',
      body: JSON.stringify({ file_name: fileName, data }),
    }),
  listCases: () => request<ApiResponse<{ items: string[] }>>('/cases'),
  resetCaseExperiment: (caseId: string, confirmCaseId: string, criteriaFileName: string) =>
    request<
      ApiResponse<{
        case_id: string
        criteria_file_name: string
        rubric_version: string
        cleared: Record<string, number>
        restored: Record<string, number>
      }>
    >(`/cases/${encodeURIComponent(caseId)}/debug/reset-experiment`, {
      method: 'POST',
      body: JSON.stringify({
        confirm_case_id: confirmCaseId,
        criteria_file_name: criteriaFileName,
      }),
    }),
  listCaseReports: (caseId: string) =>
    request<
      ApiResponse<{
        items: Array<{ name: string; markdown_path: string | null; result_path: string | null }>
      }>
    >(`/cases/${encodeURIComponent(caseId)}/reports`),
  getCaseReport: (caseId: string, reportName: string, criteriaFileName?: string) =>
    request<ApiResponse<{ name: string; markdown: string | null; result: GradingResult | null }>>(
      `/cases/${encodeURIComponent(caseId)}/reports/${encodeURIComponent(reportName)}${
        criteriaFileName ? `?criteria_file_name=${encodeURIComponent(criteriaFileName)}` : ''
      }`,
    ),
  getCaseAlignment: (caseId: string, criteriaFileName?: string) =>
    request<ApiResponse<AlignmentState | Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/alignment${criteriaFileName ? `?criteria_file_name=${encodeURIComponent(criteriaFileName)}` : ''}`,
    ),
  getCohortSynthesis: (
    caseId: string,
    payload: { criteria_file_name?: string; criterion_id?: string },
  ) =>
    request<ApiResponse<CohortSynthesis>>(`/cases/${encodeURIComponent(caseId)}/cohort-synthesis`, {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: AUDIT_REQUEST_TIMEOUT_MS,
    }),
  evaluateCaseAlignment: (
    caseId: string,
    payload?: { criteria_id?: string; criteria_file_name?: string },
  ) =>
    request<ApiResponse<AlignmentState | Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/alignment/evaluate`,
      {
        method: 'POST',
        body: JSON.stringify(payload || {}),
      },
    ),
  auditCaseAlignment: (
    caseId: string,
    payload?: { criteria_id?: string; criteria_ids?: string[]; criteria_file_name?: string },
  ) =>
    request<ApiResponse<Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/alignment/audit`,
      {
        method: 'POST',
        body: JSON.stringify(payload || {}),
        timeoutMs: AUDIT_REQUEST_TIMEOUT_MS,
      },
    ),
  resolveAlignmentConflict: (
    caseId: string,
    conflictId: string,
    payload: {
      selected_pattern_id: string
      teacher_reason?: string
    },
  ) =>
    request<ApiResponse<Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/alignment/conflicts/${encodeURIComponent(conflictId)}/resolve`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  authorizeCriterionBatch: (caseId: string, criterionId: string, criteriaFileName?: string) =>
    request<ApiResponse<Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/alignment/criteria/${encodeURIComponent(criterionId)}/authorize`,
      { method: 'POST', body: JSON.stringify({ criteria_file_name: criteriaFileName }) },
    ),
  confirmCriterionRepair: (caseId: string, criterionId: string, criteriaFileName?: string) =>
    request<ApiResponse<Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/alignment/criteria/${encodeURIComponent(criterionId)}/repair/confirm`,
      { method: 'POST', body: JSON.stringify({ criteria_file_name: criteriaFileName }) },
    ),
  autoGradeCaseReport: (
    caseId: string,
    reportName: string,
    payload?: { criteria_file_name?: string; criteria_ids?: string[]; reason?: string },
  ) =>
    request<ApiResponse<AutoGradeResponse | Record<string, unknown>>>(
      `/cases/${encodeURIComponent(caseId)}/reports/${encodeURIComponent(reportName)}/auto-grade`,
      {
        method: 'POST',
        body: JSON.stringify(payload || {}),
        timeoutMs: AUDIT_REQUEST_TIMEOUT_MS,
      },
    ),
  createAutoGradeBatch: (
    caseId: string,
    payload: {
      criteria_file_name: string
      created_by?: string
      report_names: string[]
      start_report_name: string
    },
  ) =>
    request<ApiResponse<{ batch: AutoGradeBatch }>>(
      `/cases/${encodeURIComponent(caseId)}/auto-grade-batches`,
      { method: 'POST', body: JSON.stringify(payload), timeoutMs: AUDIT_REQUEST_TIMEOUT_MS },
    ),
  listAutoGradeBatches: (caseId: string, criteriaFileName?: string) => {
    const query = criteriaFileName
      ? `?criteria_file_name=${encodeURIComponent(criteriaFileName)}`
      : ''
    return request<ApiResponse<{ items: AutoGradeBatch[] }>>(
      `/cases/${encodeURIComponent(caseId)}/auto-grade-batches${query}`,
    )
  },
  getAutoGradeBatch: (caseId: string, batchId: string) =>
    request<ApiResponse<{ batch: AutoGradeBatch }>>(
      `/cases/${encodeURIComponent(caseId)}/auto-grade-batches/${encodeURIComponent(batchId)}`,
    ),
  confirmAutoGradeBatch: (caseId: string, batchId: string, confirmedBy = 'teacher') =>
    request<ApiResponse<{ batch: AutoGradeBatch }>>(
      `/cases/${encodeURIComponent(caseId)}/auto-grade-batches/${encodeURIComponent(batchId)}/confirm`,
      {
        method: 'POST',
        body: JSON.stringify({ confirmed_by: confirmedBy }),
        timeoutMs: AUDIT_REQUEST_TIMEOUT_MS,
      },
    ),
  cancelAutoGradeBatch: (caseId: string, batchId: string, cancelledBy = 'teacher') =>
    request<ApiResponse<{ batch: AutoGradeBatch }>>(
      `/cases/${encodeURIComponent(caseId)}/auto-grade-batches/${encodeURIComponent(batchId)}/cancel`,
      {
        method: 'POST',
        body: JSON.stringify({ cancelled_by: cancelledBy }),
      },
    ),
  saveCaseReportGrade: (
    caseId: string,
    reportName: string,
    payload: {
      criteria_id: string
      criteria_score: number
      score_reason?: string
      anchor_text?: string
      evidence_anchors?: EvidenceAnchor[]
      target_section?: string | null
      criteria_file_name: string
      matched_scoring_points?: string[]
      matched_deduction_rules?: string[]
      scoring_point_scores?: ScoringPointScore[]
    },
  ) =>
    request<ApiResponse<{ result: GradingResult }>>(
      `/cases/${encodeURIComponent(caseId)}/reports/${encodeURIComponent(reportName)}/grade`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  acceptCaseReportModelGrade: (
    caseId: string,
    reportName: string,
    payload: { criteria_id: string; criteria_file_name?: string; accepted_by?: string },
  ) =>
    request<
      ApiResponse<{
        result: GradingResult
        accepted: boolean
        duplicate: boolean
        event?: Record<string, unknown>
      }>
    >(
      `/cases/${encodeURIComponent(caseId)}/reports/${encodeURIComponent(reportName)}/grade/accept-model`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  deleteCaseReportGrade: (
    caseId: string,
    reportName: string,
    payload: { criteria_id: string; criteria_file_name?: string },
  ) =>
    request<ApiResponse<{ result: GradingResult }>>(
      `/cases/${encodeURIComponent(caseId)}/reports/${encodeURIComponent(reportName)}/grade/delete`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  listImplicitGrades: (caseId: string, criteriaFileName?: string) =>
    request<ApiResponse<{ items: ImplicitGradeItem[] }>>(
      `/cases/${encodeURIComponent(caseId)}/implicit-grades${
        criteriaFileName ? `?criteria_file_name=${encodeURIComponent(criteriaFileName)}` : ''
      }`,
    ),
  createImplicitGrade: (
    caseId: string,
    payload: {
      report_name: string
      anchor: EvidenceAnchor
      score: number
      max_score?: number | null
      notes?: string | null
      temporary_label?: string | null
      matched_scoring_points?: string[]
      matched_deduction_rules?: string[]
      scoring_point_scores?: ScoringPointScore[]
      criteria_file_name?: string | null
      request_id?: string | null
    },
  ) =>
    request<ApiResponse<{ item: ImplicitGradeItem }>>(
      `/cases/${encodeURIComponent(caseId)}/implicit-grades`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),
  updateImplicitGrade: (
    caseId: string,
    implicitGradeId: string,
    payload: Partial<ImplicitGradeItem>,
  ) =>
    request<ApiResponse<{ item: ImplicitGradeItem }>>(
      `/cases/${encodeURIComponent(caseId)}/implicit-grades/${encodeURIComponent(implicitGradeId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
    ),
  retryImplicitGrade: (caseId: string, implicitGradeId: string) =>
    request<ApiResponse<{ item: ImplicitGradeItem }>>(
      `/cases/${encodeURIComponent(caseId)}/implicit-grades/${encodeURIComponent(implicitGradeId)}/retry`,
      { method: 'POST' },
    ),
  deleteImplicitGrade: (caseId: string, implicitGradeId: string) =>
    request<ApiResponse<{ item: ImplicitGradeItem }>>(
      `/cases/${encodeURIComponent(caseId)}/implicit-grades/${encodeURIComponent(implicitGradeId)}`,
      { method: 'DELETE' },
    ),
  listCriterionProposals: (caseId: string, criteriaFileName?: string) =>
    request<ApiResponse<{ items: CriterionProposalItem[] }>>(
      `/cases/${encodeURIComponent(caseId)}/criterion-proposals${
        criteriaFileName ? `?criteria_file_name=${encodeURIComponent(criteriaFileName)}` : ''
      }`,
    ),
  confirmCriterionProposal: (
    caseId: string,
    proposalId: string,
    payload: {
      proposal_version: number
      suggested_criterion: CriteriaItem
      target_criterion_id?: string | null
      criteria_file_name?: string | null
      confirmed_by?: string | null
    },
  ) =>
    request<ApiResponse<{ item: CriterionProposalItem }>>(
      `/cases/${encodeURIComponent(caseId)}/criterion-proposals/${encodeURIComponent(proposalId)}/confirm`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  rejectCriterionProposal: (
    caseId: string,
    proposalId: string,
    payload: { proposal_version: number; reason?: string | null },
  ) =>
    request<ApiResponse<{ item: CriterionProposalItem }>>(
      `/cases/${encodeURIComponent(caseId)}/criterion-proposals/${encodeURIComponent(proposalId)}/reject`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
}
