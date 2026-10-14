import type {
  AlignmentConflict,
  AlignmentReadiness,
  AlignmentState,
  AutoGradeBatch,
  AutoGradeJobStatus,
  AutoGradeJobStatusItem,
  CriterionAuthorization,
  CriterionConsistencyState,
  CriterionGlobalSummary,
  GlobalReadiness,
  GlobalReportSummary,
  GradingItem,
  GradingResult,
  ReviewAssignment,
} from '../../types/schema'

const records = (value: unknown): Record<string, unknown>[] => {
  const source = Array.isArray(value)
    ? value
    : value && typeof value === 'object'
      ? Object.values(value as Record<string, unknown>)
      : []
  return source.filter((item): item is Record<string, unknown> =>
    Boolean(item && typeof item === 'object'),
  )
}

export const createEmptyAlignmentState = (): AlignmentState => ({
  readiness: [],
  open_conflicts: [],
  criterion_authorizations: [],
  criterion_global_summaries: [],
  auto_grade_jobs: [],
  auto_grade_batches: [],
  criterion_revisions: [],
  audit_jobs: [],
  consistency_states: [],
  review_assignments: [],
  consistency_automation_mode: 'legacy',
  global_report_summaries: [],
  global_readiness: { allowed: true, blocker_count: 0, blockers: [] },
})

export const normalizeCriteriaId = (criteriaId?: string | number | null) =>
  criteriaId === null || criteriaId === undefined || criteriaId === '' ? null : String(criteriaId)

export const normalizeResult = (result: GradingResult | null): GradingResult | null => {
  if (!result) return result
  return {
    ...result,
    criteria: result.criteria.map((item) => ({ ...item, criteria_id: String(item.criteria_id) })),
  }
}

export const gradeSource = (item: GradingItem | null | undefined) => {
  const provenance = item?.provenance
  const provenanceGrader =
    provenance && typeof provenance === 'object'
      ? (provenance as Record<string, unknown>).grader_type
      : null
  return (provenanceGrader || item?.grader_type || item?.graded_by || '').toString().toLowerCase()
}

export const gradingEventId = (item: GradingItem | null | undefined) => {
  const provenance = item?.provenance
  if (provenance && typeof provenance === 'object') {
    const eventId = (provenance as Record<string, unknown>).event_id
    return eventId ? String(eventId) : null
  }
  return null
}

export const isTeacherGrade = (item: GradingItem | null | undefined) =>
  ['teacher', 'user'].includes(gradeSource(item))

export const isModelGrade = (item: GradingItem | null | undefined) =>
  ['model', 'llm'].includes(gradeSource(item))

export const gradeDecisionStatus = (item: GradingItem | null | undefined) => {
  const provenance = item?.provenance
  if (provenance && typeof provenance === 'object') {
    const status = (provenance as Record<string, unknown>).decision_status
    if (status) return String(status)
  }
  return item?.acceptance_status ? String(item.acceptance_status) : null
}

export const isAcceptedModelGrade = (item: GradingItem | null | undefined) =>
  gradeDecisionStatus(item) === 'accepted_model'

export const canAcceptModelGrade = (item: GradingItem | null | undefined) =>
  Boolean(item && isModelGrade(item) && !isAcceptedModelGrade(item))

const normalizeReadiness = (raw: unknown): AlignmentReadiness[] =>
  records(raw)
    .map((item) => {
      const criteriaId = String(item.criteria_id ?? item.criterion_id ?? item.id ?? '')
      const lifecycle = String(
        item.lifecycle_state ??
          item.lifecycle ??
          item.status ??
          item.state ??
          (item.active ? 'active_selective' : 'collecting'),
      )
      return {
        ...item,
        criteria_id: criteriaId,
        lifecycle_state: lifecycle,
      } as AlignmentReadiness
    })
    .filter((item) => item.criteria_id)

const teacherPatternEventIds = (raw: unknown): string[] | null => {
  if (!raw || typeof raw !== 'object') return null
  const pattern = raw as Record<string, unknown>
  const eventIds = Array.isArray(pattern.event_ids) ? pattern.event_ids.map(String) : []
  const representativeId = String(pattern.representative_event_id ?? '')
  if (
    !String(pattern.pattern_id ?? '') ||
    pattern.grader_type !== 'teacher' ||
    !eventIds.length ||
    !eventIds.includes(representativeId)
  ) {
    return null
  }
  return eventIds
}

const normalizeConflicts = (raw: unknown): AlignmentConflict[] =>
  records(Array.isArray(raw) ? raw : [])
    .map(
      (item) =>
        ({
          ...item,
          criteria_id: String(item.criteria_id ?? item.criterion_id ?? item.id ?? ''),
          open: item.open !== false && item.status !== 'resolved',
        }) as AlignmentConflict,
    )
    .filter((item) => {
      if (!item.criteria_id || item.conflict_type !== 'teacher_teacher') return false
      const currentIds = teacherPatternEventIds(item.current_pattern)
      const alternativeIds = teacherPatternEventIds(item.alternative_pattern)
      const teacherIds = Array.isArray(item.teacher_event_ids)
        ? item.teacher_event_ids.map(String)
        : []
      const patternIds = [...(currentIds || []), ...(alternativeIds || [])]
      return Boolean(
        currentIds &&
        alternativeIds &&
        teacherIds.length >= 2 &&
        new Set(patternIds).size === new Set(teacherIds).size &&
        teacherIds.every((eventId) => patternIds.includes(eventId)),
      )
    })

export const normalizeAutoGradeJobs = (
  raw: unknown,
  opts: { requireReportName?: boolean } = {},
): AutoGradeJobStatusItem[] => {
  const requireReportName = opts.requireReportName ?? true
  return records(raw)
    .map((item) => ({
      ...item,
      report_name: String(item.report_name ?? item.report ?? ''),
      criteria_id: String(item.criteria_id ?? item.criterion_id ?? ''),
      criterion_id: item.criterion_id ? String(item.criterion_id) : null,
      status: String(item.status ?? 'pending') as AutoGradeJobStatus,
    }))
    .filter((item) => item.criteria_id && (!requireReportName || item.report_name))
}

const normalizeCriterionSummaries = (raw: unknown): CriterionGlobalSummary[] =>
  records(Array.isArray(raw) ? raw : [])
    .map(
      (item) =>
        ({
          ...item,
          criteria_file_name: String(item.criteria_file_name ?? 'criteria.json'),
          criterion_id: String(item.criterion_id ?? item.criteria_id ?? ''),
          points: records(Array.isArray(item.points) ? item.points : []).map((point) => ({
            ...point,
            report_id: String(point.report_id ?? point.report_name ?? ''),
            report_name: String(point.report_name ?? point.report_id ?? ''),
            criterion_id: String(point.criterion_id ?? point.criteria_id ?? ''),
            event_id: point.event_id ? String(point.event_id) : null,
            anchor_text:
              point.anchor_text === null || point.anchor_text === undefined
                ? ''
                : String(point.anchor_text),
            target_section:
              point.target_section === null || point.target_section === undefined
                ? null
                : String(point.target_section),
            anchor_occurrence: Number.isFinite(Number(point.anchor_occurrence))
              ? Number(point.anchor_occurrence)
              : 0,
          })) as CriterionGlobalSummary['points'],
          eligible_report_names: Array.isArray(item.eligible_report_names)
            ? item.eligible_report_names.map(String)
            : [],
          retryable_report_names: Array.isArray(item.retryable_report_names)
            ? item.retryable_report_names.map(String)
            : [],
        }) as CriterionGlobalSummary,
    )
    .filter((item) => item.criterion_id)

const normalizeConsistencyStates = (raw: unknown): CriterionConsistencyState[] =>
  records(Array.isArray(raw) ? raw : [])
    .map(
      (item) =>
        ({
          ...item,
          record_key: String(item.record_key ?? ''),
          criteria_file_name: String(item.criteria_file_name ?? 'criteria.json'),
          criterion_id: String(item.criterion_id ?? item.criteria_id ?? ''),
          state: String(item.state ?? 'calibrating'),
          state_source: String(item.state_source ?? 'evidence'),
          agreement_count: Number(item.agreement_count ?? 0),
          correction_count: Number(item.correction_count ?? 0),
          smoothed_agreement: Number(item.smoothed_agreement ?? 0.5),
          threshold: Number(item.threshold ?? 0.8),
          review_rate: Number(item.review_rate ?? 0.5),
          full_batch_recommended: item.full_batch_recommended === true,
        }) as CriterionConsistencyState,
    )
    .filter((item) => item.criterion_id)

const normalizeReviewAssignments = (raw: unknown): ReviewAssignment[] =>
  records(Array.isArray(raw) ? raw : [])
    .map(
      (item) =>
        ({
          ...item,
          model_event_id: String(item.model_event_id ?? ''),
          report_name: String(item.report_name ?? ''),
          criterion_id: String(item.criterion_id ?? item.criteria_id ?? ''),
          criteria_file_name: String(item.criteria_file_name ?? 'criteria.json'),
          review_rate: Number(item.review_rate ?? 0.5),
          review_required: item.review_required === true,
        }) as ReviewAssignment,
    )
    .filter((item) => item.model_event_id && item.report_name && item.criterion_id)

const normalizeReportSummaries = (raw: unknown): GlobalReportSummary[] =>
  records(Array.isArray(raw) ? raw : [])
    .map(
      (item) =>
        ({
          ...item,
          report_id: String(item.report_id ?? item.report_name ?? ''),
          report_name: String(item.report_name ?? item.report_id ?? ''),
          analysis_summary: String(item.analysis_summary ?? ''),
          score: item.score === null || item.score === undefined ? null : Number(item.score),
          base_score: Number(item.base_score ?? 100),
          graded_criteria_count: Number(item.graded_criteria_count ?? 0),
          total_criteria_count: Number(item.total_criteria_count ?? 0),
          conflict_count: Number(item.conflict_count ?? 0),
          partial: item.partial === true,
          provisional: item.provisional === true,
          teacher_count: Number(item.teacher_count ?? 0),
          model_count: Number(item.model_count ?? 0),
          criteria_scores:
            item.criteria_scores && typeof item.criteria_scores === 'object'
              ? item.criteria_scores
              : {},
        }) as GlobalReportSummary,
    )
    .filter((item) => item.report_id)

const normalizeGlobalReadiness = (raw: unknown): GlobalReadiness =>
  raw && typeof raw === 'object'
    ? ({
        ...(raw as Record<string, unknown>),
        allowed: (raw as Record<string, unknown>).allowed === true,
        blocker_count: Number((raw as Record<string, unknown>).blocker_count ?? 0),
        blockers: Array.isArray((raw as Record<string, unknown>).blockers)
          ? ((raw as Record<string, unknown>).blockers as GlobalReadiness['blockers'])
          : [],
      } as GlobalReadiness)
    : { allowed: true, blocker_count: 0, blockers: [] }

const normalizeAutoGradeBatches = (raw: unknown): AutoGradeBatch[] =>
  records(raw)
    .map((item) => {
      const targets = records(item.targets).map((target) => ({
        ...target,
        report_name: String(target.report_name ?? ''),
        criterion_id: String(target.criterion_id ?? target.criteria_id ?? ''),
        status: String(target.status ?? 'pending') as AutoGradeJobStatus,
      }))
      const progressRaw =
        item.progress && typeof item.progress === 'object'
          ? (item.progress as Record<string, unknown>)
          : {}
      return {
        ...item,
        batch_id: String(item.batch_id ?? ''),
        case_id: String(item.case_id ?? ''),
        criteria_file_name: String(item.criteria_file_name ?? 'criteria.json'),
        rubric_version: String(item.rubric_version ?? ''),
        model_id: String(item.model_id ?? ''),
        status: String(item.status ?? 'awaiting_calibration'),
        report_names: Array.isArray(item.report_names) ? item.report_names.map(String) : [],
        criteria: records(item.criteria).map((criterion) => ({
          ...criterion,
          criterion_id: String(criterion.criterion_id ?? criterion.criteria_id ?? ''),
          max_score:
            criterion.max_score === null || criterion.max_score === undefined
              ? null
              : Number(criterion.max_score),
        })),
        targets,
        progress: {
          total: Number(progressRaw.total ?? targets.length),
          completed: Number(progressRaw.completed ?? 0),
          percent: Number(progressRaw.percent ?? 0),
          pending: Number(progressRaw.pending ?? 0),
          running: Number(progressRaw.running ?? 0),
          done: Number(progressRaw.done ?? 0),
          abstained: Number(progressRaw.abstained ?? 0),
          failed: Number(progressRaw.failed ?? 0),
          stale: Number(progressRaw.stale ?? 0),
          superseded: Number(progressRaw.superseded ?? 0),
          cancelled: Number(progressRaw.cancelled ?? 0),
        },
      } as AutoGradeBatch
    })
    .filter((item) => item.batch_id)

export const normalizeAlignment = (payload: unknown): AlignmentState => {
  const obj = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  const readinessContainer =
    obj.readiness ?? obj.criteria_readiness ?? obj.items ?? obj.readiness_by_criteria
  const readinessRaw =
    readinessContainer &&
    typeof readinessContainer === 'object' &&
    !Array.isArray(readinessContainer)
      ? ((readinessContainer as Record<string, unknown>).criteria ?? readinessContainer)
      : readinessContainer
  const authorizationsRaw = obj.criterion_authorizations ?? obj.criterionAuthorizations

  return {
    readiness: normalizeReadiness(readinessRaw),
    open_conflicts: normalizeConflicts(obj.open_conflicts ?? obj.conflicts ?? obj.openConflicts),
    criterion_authorizations: records(authorizationsRaw)
      .map(
        (item) =>
          ({
            ...item,
            criteria_file_name: String(item.criteria_file_name ?? 'criteria.json'),
            criterion_id: String(item.criterion_id ?? item.criteria_id ?? ''),
            state: String(item.state ?? 'teacher_validation'),
          }) as CriterionAuthorization,
      )
      .filter((item) => item.criterion_id),
    criterion_global_summaries: normalizeCriterionSummaries(
      obj.criterion_global_summaries ?? obj.criterionGlobalSummaries,
    ),
    auto_grade_jobs: normalizeAutoGradeJobs(
      obj.auto_grade_jobs ?? obj.autoGradeJobs ?? obj.auto_grade_status,
    ),
    auto_grade_batches: normalizeAutoGradeBatches(obj.auto_grade_batches ?? obj.autoGradeBatches),
    criterion_revisions: records(obj.criterion_revisions ?? obj.criterionRevisions).map((item) => ({
      ...item,
      revision_id: String(item.revision_id ?? ''),
      case_id: String(item.case_id ?? ''),
      criteria_file_name: String(item.criteria_file_name ?? 'criteria.json'),
      criterion_id: String(item.criterion_id ?? item.criteria_id ?? ''),
      target_rubric_version: String(item.target_rubric_version ?? ''),
      target_criterion_hash: String(item.target_criterion_hash ?? ''),
      status: String(item.status ?? 'awaiting_audit'),
      report_names: Array.isArray(item.report_names) ? item.report_names.map(String) : [],
      created_at: String(item.created_at ?? ''),
      updated_at: String(item.updated_at ?? ''),
    })),
    audit_jobs: normalizeAutoGradeJobs(obj.audit_jobs ?? obj.auditJobs, {
      requireReportName: false,
    }),
    consistency_states: normalizeConsistencyStates(obj.consistency_states ?? obj.consistencyStates),
    review_assignments: normalizeReviewAssignments(obj.review_assignments ?? obj.reviewAssignments),
    consistency_automation_mode: String(
      obj.consistency_automation_mode ?? 'legacy',
    ) as AlignmentState['consistency_automation_mode'],
    global_report_summaries: normalizeReportSummaries(
      obj.global_report_summaries ?? obj.globalReportSummaries,
    ),
    global_readiness: normalizeGlobalReadiness(obj.global_readiness ?? obj.globalReadiness),
    global_projection_hash: obj.global_projection_hash ? String(obj.global_projection_hash) : null,
  }
}
