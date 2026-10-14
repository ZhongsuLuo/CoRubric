import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  CriteriaSchema,
  CriteriaCategoryPreview,
  GradingResult,
  GlobalScorePoint,
  HighlightItem,
  ReportItem,
  CriteriaMode,
  HighlightAnchor,
  AlignmentConflictEventRef,
  AlignmentState,
  AutoGradeJobStatus,
  AutoGradeJobStatusItem,
  AutoGradeAbstention,
  AutoGradeResponse,
  AutoGradeBatch,
  CriterionProposalItem,
  ImplicitGradeItem,
  ScoringPointScore,
} from '../types/schema'
import { api } from '../api/client'
import { gradingVisualConfig } from '../config/gradingVisualConfig'
import { normalizeGlobalZoom } from '../utils/globalView'
import { evidenceAnchorsWithLegacyImages } from '../utils/imageEvidence'
import { mergeContiguousTextEvidenceAnchors } from '../utils/evidenceSegments'
import { scoringPointDeductionCapacity } from '../utils/scoringPoints'
import {
  isTerminalAutoGradeBatch,
  TERMINAL_AUTO_GRADE_TARGET_STATUSES,
} from '../utils/autoGradeBatch'
import {
  canAcceptModelGrade,
  createEmptyAlignmentState,
  gradeSource,
  gradingEventId,
  isAcceptedModelGrade,
  isModelGrade,
  isTeacherGrade,
  normalizeAlignment,
  normalizeCriteriaId,
  normalizeResult,
} from './grading/normalizers'

const uid = () => crypto.randomUUID()
const UNINDUCED_CRITERIA_ID = '__uninduced_grading__'
const INDUCTION_REFRESH_INITIAL_DELAY_MS = 1200
const INDUCTION_REFRESH_INTERVAL_MS = 3000
const INDUCTION_REFRESH_TIMEOUT_MS = 90000
// Archived adaptive scheduling remains below for maintenance compatibility,
// but production navigation and alignment refreshes never invoke it. See
// the local history archive for the retired lookahead policy.
const LEGACY_ADAPTIVE_SCHEDULER_ENABLED = false
const PRE_GRADE_LOOKAHEAD_COUNT = 2
const cloneCriteria = (schema: CriteriaSchema) =>
  JSON.parse(JSON.stringify(schema)) as CriteriaSchema
const toFileName = (title?: string) => {
  const base =
    (title || 'criteria')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'criteria'
  return `${base}.json`
}

const ATOMIC_ABSORPTION_CRITERIA_FILE =
  '“原子吸收光谱法测定饮用水中的镁含量”实验评分标准_criterion_categories_v2.json'
const CASE_ONE_CRITERIA_FILE = 'Case 1 评分标准.json'
const CASE_CRITERIA_DEFAULTS: Record<string, string> = {
  'case 1': CASE_ONE_CRITERIA_FILE,
  'case 2': ATOMIC_ABSORPTION_CRITERIA_FILE,
}
const EXPERIMENT_RUBRIC_BASELINES = CASE_CRITERIA_DEFAULTS

type AutoGradeQueueReason = 'manual' | 'backfill' | 'pregrade' | 'benchmark' | 'repair'
type AutoGradeQueueJob = {
  caseId: string
  criteriaFileName: string
  reportId: string
  criteriaIds?: string[]
  priority: number
  reason: AutoGradeQueueReason
}

type TeacherHighlightSavePayload = {
  report_id: string
  criteria_id?: string | null
  score: number
  max_score?: number | null
  reason: string
  anchor: HighlightAnchor
  target_section?: string | null
  matched_scoring_points?: string[]
  matched_deduction_rules?: string[]
  scoring_point_scores?: ScoringPointScore[]
}

export const useGradingStore = defineStore('grading', () => {
  const reports = ref<ReportItem[]>([])
  const cases = ref<string[]>([])
  const activeCaseId = ref<string>('case 1')

  const criteriaFiles = ref<string[]>([])
  const activeCriteriaFile = ref<string>('')
  const loadedCriteriaSchema = ref<CriteriaSchema | null>(null)
  const criteriaSchema = ref<CriteriaSchema | null>(null)
  const gradingResults = ref<Record<string, GradingResult>>({})
  const highlightsByReport = ref<Record<string, HighlightItem[]>>({})
  const implicitGrades = ref<ImplicitGradeItem[]>([])
  const criterionProposals = ref<CriterionProposalItem[]>([])
  const implicitHighlightsByReport = ref<Record<string, HighlightItem[]>>({})
  const alignment = ref<AlignmentState>(createEmptyAlignmentState())
  const alignmentLoading = ref(false)
  const alignmentError = ref('')
  const abstentionsByReport = ref<Record<string, AutoGradeAbstention[]>>({})
  const autoGradeStatusByReport = ref<
    Record<string, 'idle' | 'running' | 'done' | 'failed' | 'cancelled'>
  >({})
  const inductionRefreshTimers = new Map<string, number>()
  const autoGradeQueue: AutoGradeQueueJob[] = []
  const queuedAutoGradeKeys = new Set<string>()
  const completedAutoGradeKeys = new Set<string>()
  const refreshedAutoGradeBatchTargets = new Set<string>()
  const autoGradeQueueRunning = ref(false)
  const autoGradeBatchStarted = ref(false)

  const activeReportId = ref<string>('')
  const highlightFocusToken = ref(0)
  const evidenceAdjustmentToken = ref(0)
  const activeCriteriaId = ref<string | null>(null)
  const activeImplicitGradeId = ref<string | null>(null)
  const activeProposalId = ref<string | null>(null)
  const activeHighlightId = ref<string | null>(null)
  const mode = ref<CriteriaMode>('single')
  const globalSelectedCriterionId = ref<string | null>(null)
  const globalReportSortDirection = ref<'asc' | 'desc'>('asc')
  const globalFocusedReportId = ref<string | null>(null)
  const globalZoomByCase = ref<Record<string, number>>({})
  const globalReportFocus = ref<{
    reportId: string
    criterionId: string
    eventId: string | null
    token: number
  } | null>(null)
  let globalReportFocusSequence = 0
  const descriptionDiff = ref<{ before: string; after: string } | null>(null)
  const diffVisible = ref(false)

  const activeReport = computed(
    () => reports.value.find((r) => r.id === activeReportId.value) || null,
  )
  const activeCriteria = computed(() => {
    if (!criteriaSchema.value || !activeCriteriaId.value) return null
    return (
      criteriaSchema.value.criteria.find((c) => String(c.criteria_id) === activeCriteriaId.value) ||
      null
    )
  })

  function conflictAwareHighlight(item: HighlightItem): HighlightItem {
    if (item.event_id && conflictHighlightEventIds.value.has(String(item.event_id))) {
      return item.color === gradingVisualConfig.conflict.highlightColor
        ? item
        : { ...item, color: gradingVisualConfig.conflict.highlightColor }
    }
    return item
  }

  const activeHighlights = computed(() => {
    const criteriaId = activeCriteriaId.value
    if (!criteriaId) {
      return (highlightsByReport.value[activeReportId.value] || []).map(conflictAwareHighlight)
    }
    if (criteriaId === UNINDUCED_CRITERIA_ID) {
      return implicitHighlightsByReport.value[activeReportId.value] || []
    }
    return (highlightsByReport.value[activeReportId.value] || [])
      .filter((item) => String(item.criteria_id) === String(criteriaId))
      .map(conflictAwareHighlight)
  })

  const activeImplicitGrade = computed(() => {
    if (!activeImplicitGradeId.value) return null
    return (
      implicitGrades.value.find((item) => item.implicit_grade_id === activeImplicitGradeId.value) ||
      null
    )
  })

  const normalizeCriteriaFileName = (fileName?: string | null) => {
    const name = (fileName || '').trim()
    if (!name) return ''
    return name.toLowerCase().endsWith('.json') ? name : `${name}.json`
  }

  const belongsToActiveCriteriaFile = (criteriaFileName?: string | null) =>
    normalizeCriteriaFileName(criteriaFileName) ===
    normalizeCriteriaFileName(activeCriteriaFile.value)

  const implicitGradeBelongsToActiveCriteriaFile = (item: ImplicitGradeItem) =>
    belongsToActiveCriteriaFile(item.criteria_file_name)

  const activeCriteriaImplicitGrades = computed(() =>
    implicitGrades.value.filter(
      (item) =>
        implicitGradeBelongsToActiveCriteriaFile(item) &&
        item.status !== 'archived' &&
        item.status !== 'confirmed',
    ),
  )

  const currentReportImplicitGrades = computed(() =>
    activeCriteriaImplicitGrades.value.filter((item) => item.report_name === activeReportId.value),
  )

  const currentReportPendingProposals = computed(() => {
    const currentImplicitIds = new Set(
      currentReportImplicitGrades.value.map((item) => item.implicit_grade_id),
    )
    return criterionProposals.value.filter(
      (item) =>
        item.status === 'pending' &&
        belongsToActiveCriteriaFile(item.criteria_file_name) &&
        item.source_implicit_grade_ids.some((id) => currentImplicitIds.has(id)),
    )
  })

  const reportHighlights = computed(() => {
    const reportId = activeReportId.value
    return [
      ...(highlightsByReport.value[reportId] || []).map(conflictAwareHighlight),
      ...(implicitHighlightsByReport.value[reportId] || []),
    ]
  })

  const pendingProposals = computed(() =>
    criterionProposals.value.filter(
      (item) => item.status === 'pending' && belongsToActiveCriteriaFile(item.criteria_file_name),
    ),
  )

  const currentReportScore = computed(() => {
    const result = gradingResults.value[activeReportId.value]
    return result?.file_score ?? 0
  })

  const readinessByCriteria = computed(
    () => new Map(alignment.value.readiness.map((item) => [String(item.criteria_id), item])),
  )

  const openConflicts = computed(() =>
    alignment.value.open_conflicts.filter(
      (item) => item.open !== false && item.status !== 'resolved',
    ),
  )

  const conflictCount = computed(() => {
    return openConflicts.value.length
  })

  const conflictHighlightEventIds = computed(() => {
    const ids = new Set<string>()
    openConflicts.value.forEach((conflict) => {
      const primaryIds = conflict.suspect_event_ids?.length
        ? conflict.suspect_event_ids
        : conflict.affected_event_ids?.length
          ? conflict.affected_event_ids
          : conflict.event_ids || []
      primaryIds.forEach((eventId) => {
        if (eventId) ids.add(String(eventId))
      })
    })
    return ids
  })

  const llmEnabled = computed(() =>
    alignment.value.readiness.some((item) => isCriterionModelReady(item.criteria_id)),
  )

  const activeAutoGradeBatch = computed(
    () =>
      alignment.value.auto_grade_batches.find(
        (item) =>
          item.criteria_file_name === activeCriteriaFile.value &&
          !isTerminalAutoGradeBatch(item.status),
      ) ||
      alignment.value.auto_grade_batches.find(
        (item) => item.criteria_file_name === activeCriteriaFile.value,
      ) ||
      null,
  )

  function syncAutoGradeBatchState() {
    if (!alignment.value.auto_grade_batches.length) return
    autoGradeBatchStarted.value = Boolean(
      activeAutoGradeBatch.value && !isTerminalAutoGradeBatch(activeAutoGradeBatch.value.status),
    )
  }

  function upsertAutoGradeBatch(batch: AutoGradeBatch) {
    const index = alignment.value.auto_grade_batches.findIndex(
      (item) => item.batch_id === batch.batch_id,
    )
    if (index >= 0) alignment.value.auto_grade_batches.splice(index, 1, batch)
    else alignment.value.auto_grade_batches.unshift(batch)
    syncAutoGradeBatchState()
  }

  function requireAutoGradeBatch(batch: AutoGradeBatch | null | undefined) {
    if (!batch) throw new Error('Batch service returned no batch')
    upsertAutoGradeBatch(batch)
    return batch
  }

  function setActiveReport(reportId: string) {
    activeReportId.value = reportId
    activeCriteriaId.value = null
    activeHighlightId.value = null
    activeImplicitGradeId.value = null
    globalReportFocus.value = null
    if (LEGACY_ADAPTIVE_SCHEDULER_ENABLED) queuePreGradesFrom(reportId)
  }

  function setActiveCase(caseId: string) {
    activeCaseId.value = caseId
    globalReportFocus.value = null
  }

  function getCaseDefaultCriteria(caseId: string) {
    return CASE_CRITERIA_DEFAULTS[caseId]
  }

  function isExperimentResetAvailable() {
    const baselineFile = EXPERIMENT_RUBRIC_BASELINES[activeCaseId.value]
    return Boolean(baselineFile && baselineFile === activeCriteriaFile.value)
  }

  function setActiveCriteria(criteriaId: string | number | null) {
    const normalizedCriteriaId = normalizeCriteriaId(criteriaId)
    if (globalReportFocus.value?.criterionId !== normalizedCriteriaId) {
      globalReportFocus.value = null
    }
    activeCriteriaId.value = normalizedCriteriaId
    if (normalizedCriteriaId !== UNINDUCED_CRITERIA_ID) {
      activeImplicitGradeId.value = null
    }
    const activeHighlight = activeHighlightId.value
      ? activeHighlights.value.find((item) => item.id === activeHighlightId.value)
      : null
    if (!activeHighlight) {
      activeHighlightId.value = null
    }
  }

  function setActiveImplicitGrade(implicitGradeId: string | null) {
    activeImplicitGradeId.value = implicitGradeId
    if (implicitGradeId) {
      activeCriteriaId.value = UNINDUCED_CRITERIA_ID
    }
  }

  function setActiveProposal(proposalId: string | null) {
    activeProposalId.value = proposalId
  }

  function setActiveHighlight(highlightId: string | null) {
    activeHighlightId.value = highlightId
  }

  function focusHighlight(highlightId: string | null) {
    activeHighlightId.value = highlightId
    highlightFocusToken.value += 1
  }

  function requestEvidenceAdjustment(criteriaId: string, highlightId: string | null) {
    activeCriteriaId.value = criteriaId
    activeHighlightId.value = highlightId
    evidenceAdjustmentToken.value += 1
  }

  function toggleMode(next?: CriteriaMode) {
    const previous = mode.value
    mode.value = next ?? (mode.value === 'single' ? 'global' : 'single')
    if (mode.value === 'global') {
      autoGradeQueue.length = 0
      queuedAutoGradeKeys.clear()
      return
    }
    clearGlobalReportFocus()
    globalReportFocus.value = null
    if (previous === 'global' && activeCaseId.value && reports.value.length) {
      void recoverAutoGradesForCase()
    }
  }

  function setGlobalSelectedCriterion(criteriaId: string | null) {
    globalSelectedCriterionId.value = normalizeCriteriaId(criteriaId)
    resetGlobalCriterionDrilldown()
  }

  function clearGlobalReportFocus() {
    globalFocusedReportId.value = null
  }

  function resetGlobalCriterionDrilldown() {
    clearGlobalReportFocus()
  }

  function resetGlobalViewState() {
    globalSelectedCriterionId.value = null
    resetGlobalCriterionDrilldown()
  }

  function setGlobalFocusedReport(reportId: string | null) {
    globalFocusedReportId.value = reportId
  }

  function setGlobalZoom(value: number) {
    const safe = normalizeGlobalZoom(value)
    globalZoomByCase.value = {
      ...globalZoomByCase.value,
      [activeCaseId.value]: safe,
    }
  }

  function inspectInReportView(
    reportId: string,
    criterionId?: string | null,
    eventId?: string | null,
  ) {
    activeReportId.value = reportId
    activeCriteriaId.value = normalizeCriteriaId(criterionId)
    activeImplicitGradeId.value = null
    const highlightId = eventId ? `event-${eventId}` : null
    activeHighlightId.value =
      highlightId &&
      (highlightsByReport.value[reportId] || []).some((item) => item.id === highlightId)
        ? highlightId
        : null
    mode.value = 'single'
    globalReportFocus.value = null
  }

  function focusGlobalPoint(point: GlobalScorePoint) {
    const reportId = String(point.report_id || point.report_name)
    const criterionId = String(point.criterion_id || point.criteria_id || '')
    if (!reportId || !criterionId) return false

    activeReportId.value = reportId
    activeCriteriaId.value = criterionId
    activeImplicitGradeId.value = null
    const eventHighlightId = point.event_id ? `event-${point.event_id}` : null
    activeHighlightId.value =
      eventHighlightId &&
      (highlightsByReport.value[reportId] || []).some((item) => item.id === eventHighlightId)
        ? eventHighlightId
        : null
    globalReportFocusSequence += 1
    globalReportFocus.value = {
      reportId,
      criterionId,
      eventId: point.event_id ? String(point.event_id) : null,
      token: globalReportFocusSequence,
    }
    return true
  }

  function addTeacherHighlight(payload: Omit<HighlightItem, 'id' | 'source' | 'color'>) {
    const highlight: HighlightItem = {
      ...payload,
      id: uid(),
      source: 'teacher',
      color: 'blue',
    }
    const bucket = (highlightsByReport.value[highlight.report_id] ??= [])
    bucket.push(highlight)
    activeHighlightId.value = highlight.id
    activeCriteriaId.value = String(highlight.criteria_id)
    return highlight
  }

  function resolveTeacherScore(payload: TeacherHighlightSavePayload) {
    const rawScore = Number.isFinite(payload.score) ? payload.score : 0
    const maxScoreValue = payload.max_score ?? activeCriteria.value?.max_score ?? null
    const resolvedMax =
      Number.isFinite(maxScoreValue as number) && maxScoreValue !== null
        ? Math.max(maxScoreValue as number, 0)
        : Math.max(rawScore, 0)
    return {
      safeScore: Math.min(rawScore, resolvedMax),
      resolvedMax,
    }
  }

  function addOptimisticTeacherHighlight(
    payload: TeacherHighlightSavePayload,
    criteriaId: string,
    safeScore: number,
  ) {
    return addTeacherHighlight({
      report_id: payload.report_id,
      criteria_id: criteriaId,
      score: safeScore,
      reason: payload.reason,
      anchor: { ...payload.anchor, occurrence: payload.anchor.occurrence ?? 0 },
      graded_by: 'user',
      provenance: 'teacher',
      lifecycle_state: getCriterionReadiness(criteriaId)?.lifecycle_state,
    })
  }

  function buildTeacherGradeRequest(
    payload: TeacherHighlightSavePayload,
    criteriaId: string,
    safeScore: number,
  ) {
    return {
      criteria_id: criteriaId,
      criteria_score: safeScore,
      score_reason: payload.reason,
      anchor_text: payload.anchor.text,
      evidence_anchors: [
        {
          anchor_type: payload.anchor.anchor_type || 'text',
          text: payload.anchor.text,
          occurrence: payload.anchor.occurrence ?? 0,
          start_offset: payload.anchor.start_offset ?? null,
          end_offset: payload.anchor.end_offset ?? null,
          image_src: payload.anchor.image_src ?? null,
          image_alt: payload.anchor.image_alt ?? null,
          description: payload.anchor.description ?? null,
          region: payload.anchor.region ?? null,
          expected_location: payload.anchor.expected_location ?? null,
          missing_item: payload.anchor.missing_item ?? null,
        },
      ],
      target_section: payload.target_section ?? null,
      matched_scoring_points: payload.matched_scoring_points || [],
      matched_deduction_rules: payload.matched_deduction_rules || [],
      scoring_point_scores: payload.scoring_point_scores || [],
      criteria_file_name: activeCriteriaFile.value,
    }
  }

  function resolveTeacherPointScores(
    payload: TeacherHighlightSavePayload,
    criteriaId: string,
    score: number,
  ): ScoringPointScore[] {
    if (payload.scoring_point_scores?.length) return payload.scoring_point_scores
    const existing = gradingResults.value[payload.report_id]?.criteria.find(
      (item) => String(item.criteria_id) === criteriaId,
    )?.scoring_point_scores
    if (existing?.length) return existing
    const points =
      criteriaSchema.value?.criteria.find((item) => String(item.criteria_id) === criteriaId)
        ?.scoring_points || []
    if (points.length !== 1) return []
    return [
      {
        point_id: points[0]!.point_id,
        score,
        max_score: scoringPointDeductionCapacity(points[0]!),
      },
    ]
  }

  function openDiff(before: string, after: string) {
    descriptionDiff.value = { before, after }
    diffVisible.value = true
  }

  function closeDiff() {
    diffVisible.value = false
  }

  function mergeAutoGradeResult(reportId: string, incoming: GradingResult) {
    const previous = gradingResults.value[reportId]
    if (!previous) return incoming
    const byCriteria = new Map(previous.criteria.map((item) => [String(item.criteria_id), item]))
    incoming.criteria.forEach((item) => {
      const criteriaId = String(item.criteria_id)
      const existing = byCriteria.get(criteriaId)
      if (isTeacherGrade(existing) && isModelGrade(item)) return
      byCriteria.set(criteriaId, item)
    })
    return (
      normalizeResult({
        ...incoming,
        criteria: Array.from(byCriteria.values()),
      }) || incoming
    )
  }

  function getCriterionReadiness(criteriaId: string | null | undefined) {
    if (!criteriaId) return null
    return readinessByCriteria.value.get(String(criteriaId)) || null
  }

  function isCriterionModelReady(criteriaId: string | null | undefined) {
    const readiness = getCriterionReadiness(criteriaId)
    return readiness?.lifecycle_state === 'active_selective' || readiness?.active === true
  }

  function getCriterionConflicts(criteriaId: string | null | undefined) {
    if (!criteriaId) return []
    return openConflicts.value.filter((item) => String(item.criteria_id) === String(criteriaId))
  }

  function getCriterionAuthorization(criteriaId: string | null | undefined) {
    if (!criteriaId) return null
    return (
      alignment.value.criterion_authorizations.find(
        (item) =>
          String(item.criterion_id) === String(criteriaId) &&
          belongsToActiveCriteriaFile(item.criteria_file_name),
      ) || null
    )
  }

  function getCriterionConsistency(criteriaId: string | null | undefined) {
    if (!criteriaId) return null
    return (
      (alignment.value.consistency_states || []).find(
        (item) =>
          String(item.criterion_id) === String(criteriaId) &&
          belongsToActiveCriteriaFile(item.criteria_file_name),
      ) || null
    )
  }

  function getReviewAssignment(reportId: string, criteriaId: string | null | undefined) {
    if (!criteriaId) return null
    return (
      (alignment.value.review_assignments || []).find(
        (item) =>
          item.report_name === reportId &&
          String(item.criterion_id) === String(criteriaId) &&
          belongsToActiveCriteriaFile(item.criteria_file_name),
      ) || null
    )
  }

  function isCriterionFullBatchAllowed(criteriaId: string) {
    const authorization = getCriterionAuthorization(criteriaId)
    const consistencyState = getCriterionConsistency(criteriaId)
    if (
      alignment.value.consistency_automation_mode === 'adaptive' &&
      consistencyState?.has_open_conflict
    )
      return false
    if (authorization?.state === 'batch_authorized') return true
    return (
      alignment.value.consistency_automation_mode === 'adaptive' &&
      consistencyState?.state === 'reliable' &&
      consistencyState.full_batch_recommended === true
    )
  }

  function reliableConsistencyCriteriaIds() {
    if (alignment.value.consistency_automation_mode !== 'adaptive') {
      return new Set<string>()
    }
    return new Set(
      (alignment.value.consistency_states || [])
        .filter(
          (item) =>
            item.state === 'reliable' &&
            item.full_batch_recommended === true &&
            belongsToActiveCriteriaFile(item.criteria_file_name),
        )
        .map((item) => String(item.criterion_id)),
    )
  }

  function reconcileAdaptiveBackfillQueue() {
    if (!LEGACY_ADAPTIVE_SCHEDULER_ENABLED) return
    if (alignment.value.consistency_automation_mode !== 'adaptive') return

    for (let index = autoGradeQueue.length - 1; index >= 0; index -= 1) {
      const job = autoGradeQueue[index]
      if (!job || !['backfill', 'benchmark'].includes(job.reason)) continue
      const queuedKey = autoGradeJobKey(job)
      const requested = normalizeCriteriaIds(job.criteriaIds)
      const stillAllowed = requested.filter(isCriterionFullBatchAllowed)
      if (!stillAllowed.length) {
        queuedAutoGradeKeys.delete(queuedKey)
        autoGradeQueue.splice(index, 1)
      } else if (stillAllowed.length !== requested.length) {
        queuedAutoGradeKeys.delete(queuedKey)
        job.criteriaIds = stillAllowed
        queuedAutoGradeKeys.add(autoGradeJobKey(job))
      }
    }

    const criteriaByReport = new Map<string, Set<string>>()
    reliableConsistencyCriteriaIds().forEach((criteriaId) => {
      const summary = getCriterionGlobalSummary(criteriaId)
      const eligibleReportIds = summary?.eligible_report_names || []
      eligibleReportIds.forEach((reportId) => {
        const criteriaIds = criteriaByReport.get(reportId) || new Set<string>()
        criteriaIds.add(criteriaId)
        criteriaByReport.set(reportId, criteriaIds)
      })
    })
    reports.value.forEach((report) => {
      const criteriaIds = criteriaByReport.get(report.id)
      if (!criteriaIds?.size) return
      enqueueAutoGradeReports([report.id], {
        criteriaIds: Array.from(criteriaIds),
        reason: 'backfill',
        priority: 70,
      })
    })
  }

  function getCriterionGlobalSummary(criteriaId: string | null | undefined) {
    if (!criteriaId) return null
    return (
      alignment.value.criterion_global_summaries.find(
        (item) =>
          String(item.criterion_id) === String(criteriaId) &&
          belongsToActiveCriteriaFile(item.criteria_file_name),
      ) || null
    )
  }

  function getReportAbstentions(reportId: string | null | undefined) {
    if (!reportId) return []
    return abstentionsByReport.value[reportId] || []
  }

  function getCriterionAbstention(
    reportId: string | null | undefined,
    criteriaId: string | null | undefined,
  ) {
    if (!reportId || !criteriaId) return null
    return (
      getReportAbstentions(reportId).find(
        (item) => String(item.criteria_id) === String(criteriaId),
      ) || null
    )
  }

  function getCriterionAutoGradeJob(
    reportId: string | null | undefined,
    criteriaId: string | null | undefined,
  ) {
    if (!reportId || !criteriaId) return null
    return (
      alignment.value.auto_grade_jobs.find(
        (item) =>
          item.report_name === reportId &&
          String(item.criteria_id) === String(criteriaId) &&
          (!activeCriteriaFile.value || item.criteria_file_name === activeCriteriaFile.value),
      ) || null
    )
  }

  async function jumpToConflictEvent(ref: AlignmentConflictEventRef | null | undefined) {
    const reportName = ref?.report_name ? String(ref.report_name) : ''
    const eventId = ref?.event_id ? String(ref.event_id) : ''
    const criteriaId = normalizeCriteriaId(ref?.criteria_id ?? ref?.criterion_id)
    if (!reportName || !eventId) return false

    if (activeReportId.value !== reportName) {
      setActiveReport(reportName)
    }
    if (criteriaId) {
      setActiveCriteria(criteriaId)
    }

    const highlightId = `event-${eventId}`
    let target = (highlightsByReport.value[reportName] || []).find(
      (item) => item.id === highlightId,
    )
    if (!target) {
      await reloadReportResultForActiveCriteria(reportName)
      target = (highlightsByReport.value[reportName] || []).find((item) => item.id === highlightId)
    }
    if (!target) return false
    activeHighlightId.value = highlightId
    if (target.criteria_id) {
      setActiveCriteria(target.criteria_id)
      activeHighlightId.value = highlightId
    }
    return true
  }

  function hydrateHighlights(reportId: string, result: GradingResult | null) {
    if (!result) return
    const list: HighlightItem[] = []
    const activeCriterionIds = new Set(
      (criteriaSchema.value?.criteria || []).map((item) => String(item.criteria_id)),
    )
    result.criteria.forEach((item) => {
      const criteriaId = String(item.criteria_id)
      if (
        item.rubric_file_name &&
        activeCriteriaFile.value &&
        item.rubric_file_name !== activeCriteriaFile.value
      ) {
        return
      }
      if (
        !item.rubric_file_name &&
        activeCriteriaFile.value &&
        !activeCriterionIds.has(criteriaId)
      ) {
        return
      }
      if (criteriaId.startsWith('draft-')) {
        return
      }
      const rawGrader = gradeSource(item)
      const isModel = rawGrader === 'llm' || rawGrader === 'model'
      const readiness = getCriterionReadiness(criteriaId)
      const eventId = gradingEventId(item)
      const structuredAnchors = evidenceAnchorsWithLegacyImages(
        item.evidence_anchors,
        item.anchor_text || item.file_original_content,
      )
      const normalizedAnchors = structuredAnchors.length
        ? structuredAnchors
            .filter((anchor) => anchor.anchor_type !== 'absence')
            .map((anchor) => ({
              ...anchor,
              text: anchor.text || anchor.image_alt || anchor.image_src || anchor.description || '',
              occurrence: Number.isFinite(anchor.occurrence) ? Number(anchor.occurrence) : 0,
            }))
            .filter((anchor) => Boolean(anchor.text.trim()))
        : [
            {
              anchor_type: 'text' as const,
              text: item.anchor_text || item.file_original_content || '',
              occurrence: 0,
            },
          ].filter((anchor) => Boolean(anchor.text.trim()))
      const reportMarkdown = reports.value.find((report) => report.id === reportId)?.markdown ?? ''
      const anchors = mergeContiguousTextEvidenceAnchors(reportMarkdown, normalizedAnchors)

      anchors.forEach((anchor, anchorIndex) => {
        list.push({
          id: eventId ? `event-${eventId}${anchorIndex ? `-anchor-${anchorIndex}` : ''}` : uid(),
          report_id: reportId,
          criteria_id: criteriaId,
          event_id: eventId,
          score: item.criteria_score,
          max_score: item.max_score,
          reason: item.score_reason,
          source: isModel ? 'llm' : 'teacher',
          color:
            eventId && conflictHighlightEventIds.value.has(eventId)
              ? gradingVisualConfig.conflict.highlightColor
              : isModel
                ? gradingVisualConfig.model.highlightColor
                : gradingVisualConfig.teacher.highlightColor,
          anchor,
          graded_by: isModel ? 'model' : rawGrader === 'user' ? 'user' : 'teacher',
          provenance: isModel ? 'model' : 'teacher',
          lifecycle_state: readiness?.lifecycle_state,
        })
      })
    })
    highlightsByReport.value[reportId] = list
  }

  function applyReportResult(reportId: string, result: GradingResult | null) {
    const normalized = normalizeResult(result)
    if (normalized) {
      gradingResults.value[reportId] = normalized
      hydrateHighlights(reportId, normalized)
    } else {
      delete gradingResults.value[reportId]
      highlightsByReport.value[reportId] = []
    }
  }

  function snapshotReportResult(reportId: string): GradingResult | null {
    const result = gradingResults.value[reportId]
    return result ? (JSON.parse(JSON.stringify(result)) as GradingResult) : null
  }

  function activateLatestTeacherHighlight(
    reportId: string,
    criteriaId: string,
    anchor?: HighlightAnchor,
  ) {
    const latest = (highlightsByReport.value[reportId] || []).find(
      (item) =>
        String(item.criteria_id) === String(criteriaId) &&
        (!anchor ||
          (item.anchor.text === anchor.text && item.anchor.occurrence === anchor.occurrence)),
    )
    if (latest) {
      activeHighlightId.value = latest.id
      activeCriteriaId.value = String(latest.criteria_id)
      return true
    }
    return false
  }

  function removeAutoGradeJobStatus(reportId: string, criteriaId: string) {
    alignment.value.auto_grade_jobs = alignment.value.auto_grade_jobs.filter(
      (item) =>
        !(
          item.report_name === reportId &&
          String(item.criteria_id) === String(criteriaId) &&
          (!activeCriteriaFile.value || item.criteria_file_name === activeCriteriaFile.value)
        ),
    )
    if (abstentionsByReport.value[reportId]) {
      abstentionsByReport.value[reportId] = abstentionsByReport.value[reportId].filter(
        (item) => String(item.criteria_id) !== String(criteriaId),
      )
    }
  }

  function upsertOptimisticTeacherGrade(
    payload: TeacherHighlightSavePayload,
    criteriaId: string,
    safeScore: number,
    resolvedMax: number,
  ) {
    const meta = criteriaSchema.value?.criteria.find(
      (item) => String(item.criteria_id) === String(criteriaId),
    )
    if (!meta) return
    const previous = gradingResults.value[payload.report_id]
    const byCriteria = new Map(
      (previous?.criteria || []).map((item) => [String(item.criteria_id), item]),
    )
    byCriteria.set(criteriaId, {
      criteria_id: criteriaId,
      description: meta.description,
      original_description: meta.original_description,
      criteria_name: meta.criteria_name,
      max_score: resolvedMax,
      criteria_score: safeScore,
      file_original_content: payload.anchor.text,
      section: meta.section || '',
      target_section: payload.target_section ?? null,
      score_reason: payload.reason,
      anchor_text: payload.anchor.text,
      scoring_points: meta.scoring_points,
      scoring_aggregation: meta.scoring_aggregation,
      matched_scoring_points: payload.matched_scoring_points || [],
      matched_deduction_rules: payload.matched_deduction_rules || [],
      scoring_point_scores: payload.scoring_point_scores || [],
      graded_by: 'user',
      rubric_file_name: activeCriteriaFile.value || null,
      provenance: 'optimistic_teacher',
    })
    applyReportResult(payload.report_id, {
      file_name: previous?.file_name || `${payload.report_id}.md`,
      language: previous?.language || criteriaSchema.value?.language || 'zh',
      base_score: previous?.base_score ?? criteriaSchema.value?.base_score ?? 100,
      file_score: previous?.file_score ?? 0,
      criteria: Array.from(byCriteria.values()),
    })
  }

  async function runTeacherGradeFollowup(reportId: string, criteriaId: string) {
    try {
      await Promise.all([
        loadAlignment(activeCaseId.value),
        reloadReportResultForActiveCriteria(reportId),
      ])
      void runPostGradeStandardReview(reportId, criteriaId)
    } catch (err) {
      console.warn('[highlight] post-save alignment refresh failed', {
        err,
        reportId,
        criteriaId,
      })
    }
  }

  async function runPostGradeStandardReview(
    reportId: string,
    criteriaId: string,
    options: { backfillPrevious?: boolean } = {},
  ) {
    try {
      clearCompletedAutoGradeJobs(criteriaId ? [criteriaId] : undefined)
      await auditAndEvaluateAlignment(criteriaId || undefined)
      if (options.backfillPrevious && reports.value.findIndex((item) => item.id === reportId) > 0) {
        // Only a newly created criterion can require historical coverage. A
        // direct card grade or a review-time match to an existing criterion
        // must not regrade earlier reports.
        queueBackfillBefore(reportId, [criteriaId])
      }
      // A newly ready criterion must first be filled on the report the teacher
      // is viewing; otherwise cross-report lookahead can leave that report
      // visibly incomplete for several model calls.
      queuePreGradesFrom(reportId)
      await Promise.all([
        loadCriterionProposals(activeCaseId.value),
        reloadReportResultForActiveCriteria(reportId),
        loadAlignment(activeCaseId.value),
      ])
    } catch (err) {
      console.warn('[highlight] background standard review failed', {
        err,
        reportId,
        criteriaId,
      })
    }
  }

  function refreshSavedTeacherHighlight(
    reportId: string,
    criteriaId: string,
    result: GradingResult,
    anchor?: HighlightAnchor,
  ) {
    removeAutoGradeJobStatus(reportId, criteriaId)
    applyReportResult(reportId, result)
    void runTeacherGradeFollowup(reportId, criteriaId)
    activateLatestTeacherHighlight(reportId, criteriaId, anchor)
  }

  function normalizeImplicitItem(item: ImplicitGradeItem): ImplicitGradeItem {
    return {
      ...item,
      criteria_file_name: item.criteria_file_name || activeCriteriaFile.value || null,
    }
  }

  function upsertImplicitGrade(item: ImplicitGradeItem) {
    const normalized = normalizeImplicitItem(item)
    const idx = implicitGrades.value.findIndex(
      (entry) => entry.implicit_grade_id === normalized.implicit_grade_id,
    )
    if (idx >= 0) implicitGrades.value[idx] = normalized
    else implicitGrades.value.push(normalized)
    hydrateImplicitHighlights()
    return normalized
  }

  function activateImplicitGrade(item: ImplicitGradeItem) {
    const normalized = upsertImplicitGrade(item)
    activeImplicitGradeId.value = normalized.implicit_grade_id
    activeCriteriaId.value = UNINDUCED_CRITERIA_ID
    activeHighlightId.value = `implicit-${normalized.implicit_grade_id}`
    return normalized
  }

  function hydrateImplicitHighlights() {
    const grouped: Record<string, HighlightItem[]> = {}
    implicitGrades.value.forEach((item) => {
      if (!implicitGradeBelongsToActiveCriteriaFile(item)) return
      if (item.status === 'archived' || item.status === 'confirmed') return
      const anchorText = item.anchor.text || item.anchor.image_alt || item.anchor.image_src || ''
      if (!anchorText) return
      const highlight: HighlightItem = {
        id: `implicit-${item.implicit_grade_id}`,
        report_id: item.report_name,
        criteria_id: UNINDUCED_CRITERIA_ID,
        implicit_grade_id: item.implicit_grade_id,
        proposal_id: item.proposal_id,
        score: item.score,
        reason: item.notes || item.temporary_label || '',
        source: 'implicit',
        color: 'amber',
        anchor: {
          anchor_type: item.anchor.anchor_type,
          text: anchorText,
          occurrence: item.anchor.occurrence ?? undefined,
          start_offset: item.anchor.start_offset ?? null,
          end_offset: item.anchor.end_offset ?? null,
          image_src: item.anchor.image_src ?? null,
          image_alt: item.anchor.image_alt ?? null,
          region: item.anchor.region ?? null,
        },
        status: item.status,
        provenance: 'implicit',
      }
      ;(grouped[item.report_name] ??= []).push(highlight)
    })
    implicitHighlightsByReport.value = grouped
  }

  function hasPendingInduction(caseId: string) {
    return (
      activeCaseId.value === caseId &&
      activeCriteriaFile.value &&
      implicitGrades.value.some(
        (item) =>
          implicitGradeBelongsToActiveCriteriaFile(item) &&
          (item.status === 'queued' || item.status === 'inducing'),
      )
    )
  }

  function clearInductionRefresh(caseId: string) {
    const timer = inductionRefreshTimers.get(caseId)
    if (timer) window.clearTimeout(timer)
    inductionRefreshTimers.delete(caseId)
  }

  function scheduleInductionRefresh(caseId = activeCaseId.value) {
    if (!caseId) return
    clearInductionRefresh(caseId)
    const startedAt = Date.now()

    const refresh = async () => {
      if (activeCaseId.value !== caseId) {
        clearInductionRefresh(caseId)
        return
      }
      try {
        await loadImplicitGrades(caseId)
        await loadCriterionProposals(caseId)
      } catch (err) {
        console.warn('[implicit-grading] delayed refresh failed', err)
      }

      if (!hasPendingInduction(caseId) || Date.now() - startedAt >= INDUCTION_REFRESH_TIMEOUT_MS) {
        clearInductionRefresh(caseId)
        return
      }
      inductionRefreshTimers.set(caseId, window.setTimeout(refresh, INDUCTION_REFRESH_INTERVAL_MS))
    }

    inductionRefreshTimers.set(
      caseId,
      window.setTimeout(refresh, INDUCTION_REFRESH_INITIAL_DELAY_MS),
    )
  }

  function removeHighlight(reportId: string, highlightId: string) {
    const bucket = highlightsByReport.value[reportId]
    if (!bucket) return
    const idx = bucket.findIndex((h) => h.id === highlightId)
    if (idx >= 0) bucket.splice(idx, 1)
    if (activeHighlightId.value === highlightId) {
      activeHighlightId.value = null
    }
  }

  async function loadImplicitGrades(caseId = activeCaseId.value) {
    if (!caseId) return []
    const res = await api.listImplicitGrades(caseId, activeCriteriaFile.value || undefined)
    implicitGrades.value = (res.data?.items || []).map(normalizeImplicitItem)
    const activeImplicit = activeImplicitGradeId.value
      ? implicitGrades.value.find((item) => item.implicit_grade_id === activeImplicitGradeId.value)
      : null
    if (
      activeImplicit &&
      (activeImplicit.status === 'archived' ||
        activeImplicit.status === 'confirmed' ||
        !implicitGradeBelongsToActiveCriteriaFile(activeImplicit))
    ) {
      activeImplicitGradeId.value = null
      if (activeCriteriaId.value === UNINDUCED_CRITERIA_ID) {
        activeCriteriaId.value = null
      }
      if (activeHighlightId.value === `implicit-${activeImplicit.implicit_grade_id}`) {
        activeHighlightId.value = null
      }
    }
    hydrateImplicitHighlights()
    return implicitGrades.value
  }

  async function loadCriterionProposals(caseId = activeCaseId.value) {
    if (!caseId) return []
    const res = await api.listCriterionProposals(caseId, activeCriteriaFile.value || undefined)
    criterionProposals.value = res.data?.items || []
    hydrateImplicitHighlights()
    return criterionProposals.value
  }

  async function loadCases() {
    const res = await api.listCases()
    cases.value = res.data?.items || []
    const first = cases.value[0]
    if (first && !cases.value.includes(activeCaseId.value)) {
      activeCaseId.value = first
    }
  }

  async function resetCurrentExperiment() {
    const caseId = activeCaseId.value
    const criteriaFileName = activeCriteriaFile.value
    if (!caseId) throw new Error('Select a case before resetting the experiment.')
    if (!criteriaFileName || !isExperimentResetAvailable()) {
      throw new Error('Select a configured experiment rubric before resetting.')
    }
    const res = await api.resetCaseExperiment(caseId, caseId, criteriaFileName)
    await loadCase(caseId)
    return res.data
  }

  async function loadCriteriaList() {
    const listRes = await api.listCriteria(activeCaseId.value || undefined)
    criteriaFiles.value = listRes.data?.items || []
  }

  async function loadCriteria(fileName?: string) {
    try {
      await loadCriteriaList()

      const target = fileName ?? (activeCriteriaFile.value || criteriaFiles.value[0] || '')
      activeCriteriaFile.value = target
      if (!target) {
        loadedCriteriaSchema.value = null
        criteriaSchema.value = null
        activeCriteriaId.value = null
        return
      }

      const criteriaRes = await api.getCriteria(target)
      const normalized = criteriaRes.data?.item || null
      if (normalized) {
        normalized.criteria = normalized.criteria.map((c) => ({
          ...c,
          criteria_id: String(c.criteria_id),
        }))
      }
      loadedCriteriaSchema.value = normalized
      criteriaSchema.value = normalized ? cloneCriteria(normalized) : null
      activeCriteriaId.value = null
    } catch (err) {
      console.error('[criteria] load failed', err)
      loadedCriteriaSchema.value = null
      criteriaSchema.value = null
      activeCriteriaId.value = null
    }
  }

  async function saveCriteriaFile(fileName?: string) {
    if (!criteriaSchema.value) {
      console.warn('[criteria] cannot save, schema missing')
      return
    }
    const target = fileName || activeCriteriaFile.value || toFileName(criteriaSchema.value.title)
    const caseId = activeCaseId.value && !CASE_CRITERIA_DEFAULTS[activeCaseId.value] ? activeCaseId.value : undefined
    const saved = await api.saveCriteria(target, criteriaSchema.value, caseId)
    await loadCriteria(saved?.data?.file_name || target)
    if (activeCaseId.value) {
      await loadAlignment(activeCaseId.value)
    }
  }

  async function previewCriteriaCategories() {
    if (!activeCriteriaFile.value) throw new Error('Select a criteria file first')
    const response = await api.previewCriteriaCategories(activeCriteriaFile.value)
    if (!response.data) throw new Error('Category preview returned no data')
    return response.data as CriteriaCategoryPreview
  }

  async function applyCriteriaCategories(
    sourceContentHash: string,
    assignments: Array<{ criteria_id: string; section: string }>,
  ) {
    if (!activeCriteriaFile.value) throw new Error('Select a criteria file first')
    const activeId = activeCriteriaId.value
    await api.applyCriteriaCategories(activeCriteriaFile.value, {
      source_content_hash: sourceContentHash,
      assignments,
    })
    await loadCriteria(activeCriteriaFile.value)
    if (
      activeId &&
      criteriaSchema.value?.criteria.some((item) => String(item.criteria_id) === String(activeId))
    ) {
      activeCriteriaId.value = activeId
    }
    if (activeCaseId.value) await loadAlignment(activeCaseId.value)
  }

  function clearCriteriaScopedState() {
    autoGradeBatchStarted.value = false
    activeCriteriaFile.value = ''
    loadedCriteriaSchema.value = null
    criteriaSchema.value = null
    activeCriteriaId.value = null
    activeImplicitGradeId.value = null
    activeProposalId.value = null
    activeHighlightId.value = null
    globalReportFocus.value = null
    resetGlobalViewState()
    gradingResults.value = {}
    highlightsByReport.value = {}
    implicitGrades.value = []
    criterionProposals.value = []
    implicitHighlightsByReport.value = {}
    alignment.value = createEmptyAlignmentState()
  }

  async function deleteCriteriaFile(fileName?: string) {
    const target = fileName || activeCriteriaFile.value
    if (!target) return
    await api.deleteCriteria(target)
    await loadCriteriaList()
    const next = criteriaFiles.value.find((file) => file !== target) || ''
    if (next) {
      await changeCriteriaFile(next)
    } else {
      clearCriteriaScopedState()
    }
  }

  async function deleteCriteriaItem(criteriaId: string) {
    if (!criteriaSchema.value) return
    const normalizedId = String(criteriaId)
    const targetFile = activeCriteriaFile.value || undefined
    const before = criteriaSchema.value.criteria.length
    criteriaSchema.value.criteria = criteriaSchema.value.criteria.filter(
      (item) => String(item.criteria_id) !== normalizedId,
    )
    if (criteriaSchema.value.criteria.length === before) return

    for (const report of reports.value) {
      const result = gradingResults.value[report.name]
      if (!result?.criteria?.some((item) => String(item.criteria_id) === normalizedId)) continue
      try {
        const res = await api.deleteCaseReportGrade(activeCaseId.value, report.name, {
          criteria_id: normalizedId,
          criteria_file_name: activeCriteriaFile.value || undefined,
        })
        const updated = normalizeResult(res.data?.result as GradingResult | null)
        applyReportResult(report.name, updated)
      } catch (err) {
        console.warn('[criteria] failed to delete related report grade', {
          err,
          report: report.name,
          criteriaId: normalizedId,
        })
      }
    }

    if (activeCriteriaId.value === normalizedId) {
      activeCriteriaId.value = null
      activeHighlightId.value = null
    }
    await saveCriteriaFile(targetFile)
    await reloadReportResultsForActiveCriteria()
    await loadAlignment(activeCaseId.value)
    await loadCriterionProposals(activeCaseId.value)
  }

  async function changeCriteriaFile(fileName: string) {
    autoGradeBatchStarted.value = false
    autoGradeQueue.length = 0
    queuedAutoGradeKeys.clear()
    completedAutoGradeKeys.clear()
    await loadCriteria(fileName)
    await reloadReportResultsForActiveCriteria()
    await loadAlignment(activeCaseId.value)
    await loadImplicitGrades(activeCaseId.value)
    await loadCriterionProposals(activeCaseId.value)
  }

  async function reloadReportResultsForActiveCriteria(caseId = activeCaseId.value) {
    gradingResults.value = {}
    highlightsByReport.value = {}
    activeHighlightId.value = null
    activeImplicitGradeId.value = null
    implicitHighlightsByReport.value = {}
    await Promise.all(
      reports.value.map(async (report) => {
        const detail = await api.getCaseReport(
          caseId,
          report.name,
          activeCriteriaFile.value || undefined,
        )
        if (detail.data?.result) {
          const result = normalizeResult(detail.data.result as GradingResult)
          applyReportResult(report.name, result)
        }
      }),
    )
  }

  async function reloadReportResultForActiveCriteria(
    reportName: string,
    caseId = activeCaseId.value,
  ) {
    if (!reportName) return null
    const detail = await api.getCaseReport(
      caseId,
      reportName,
      activeCriteriaFile.value || undefined,
    )
    const result = normalizeResult(detail.data?.result as GradingResult | null)
    applyReportResult(reportName, result)
    return result
  }

  async function loadAlignment(caseId = activeCaseId.value) {
    if (!caseId) return alignment.value
    alignmentLoading.value = true
    alignmentError.value = ''
    try {
      const res = await api.getCaseAlignment(caseId, activeCriteriaFile.value || undefined)
      alignment.value = normalizeAlignment(res.data)
      syncAutoGradeBatchState()
      reconcileAdaptiveBackfillQueue()
    } catch (err) {
      alignment.value = createEmptyAlignmentState()
      alignmentError.value = err instanceof Error ? err.message : 'Alignment state unavailable'
      console.warn('[alignment] load failed', err)
    } finally {
      alignmentLoading.value = false
    }
    return alignment.value
  }

  async function evaluateAlignment(criteriaId?: string) {
    alignmentLoading.value = true
    alignmentError.value = ''
    try {
      const res = await api.evaluateCaseAlignment(activeCaseId.value, {
        criteria_id: criteriaId,
        criteria_file_name: activeCriteriaFile.value || undefined,
      })
      alignment.value = normalizeAlignment(res.data)
    } catch (err) {
      alignmentError.value = err instanceof Error ? err.message : 'Readiness evaluation failed'
      console.warn('[alignment] evaluate failed', err)
    } finally {
      alignmentLoading.value = false
    }
    return alignment.value
  }

  async function auditAlignment(criteriaId?: string) {
    alignmentLoading.value = true
    alignmentError.value = ''
    try {
      const res = await api.auditCaseAlignment(activeCaseId.value, {
        criteria_id: criteriaId,
        criteria_file_name: activeCriteriaFile.value || undefined,
      })
      const data = res.data as Record<string, unknown> | null | undefined
      alignment.value = normalizeAlignment(data?.alignment ?? data)
      const auditItems = Array.isArray(data?.items) ? data.items : []
      auditItems.forEach((raw) => {
        if (!raw || typeof raw !== 'object') return
        const item = raw as Record<string, unknown>
        const job =
          item.job && typeof item.job === 'object' ? (item.job as Record<string, unknown>) : {}
        const criterionId = String(job.criterion_id ?? job.criteria_id ?? '')
        const reportNames = Array.isArray(item.model_regrade_report_names)
          ? item.model_regrade_report_names.map(String)
          : []
        if (!criterionId || !reportNames.length) return
        clearCompletedAutoGradeJobs([criterionId])
        enqueueAutoGradeReports(reportNames, {
          criteriaIds: [criterionId],
          reason: 'repair',
          priority: 65,
        })
      })
    } catch (err) {
      alignmentError.value = err instanceof Error ? err.message : 'Consistency audit failed'
      console.warn('[alignment] audit failed', err)
      throw err
    } finally {
      alignmentLoading.value = false
    }
    return alignment.value
  }

  async function auditAndEvaluateAlignment(criteriaId?: string) {
    try {
      return await auditAlignment(criteriaId)
    } catch {
      return evaluateAlignment(criteriaId)
    }
  }

  async function resolveConflict(
    conflictId: string,
    selectedPatternId: string,
    teacherReason = '',
  ) {
    const res = await api.resolveAlignmentConflict(activeCaseId.value, conflictId, {
      selected_pattern_id: selectedPatternId,
      teacher_reason: teacherReason,
    })
    const data = res.data as Record<string, unknown> | null | undefined
    alignment.value = normalizeAlignment(data?.alignment)
    if (activeReportId.value) await reloadReportResultForActiveCriteria(activeReportId.value)
    return data
  }

  async function authorizeCriterion(criteriaId: string) {
    const res = await api.authorizeCriterionBatch(
      activeCaseId.value,
      criteriaId,
      activeCriteriaFile.value || undefined,
    )
    const data = res.data as Record<string, unknown> | null | undefined
    alignment.value = normalizeAlignment(data?.alignment)
    const globalSummary = getCriterionGlobalSummary(criteriaId)
    clearCompletedAutoGradeJobs([criteriaId])
    enqueueAutoGradeReports(globalSummary?.eligible_report_names || [], {
      criteriaIds: [criteriaId],
      reason: 'backfill',
      priority: 70,
    })
    return data
  }

  async function confirmCriterionRepair(criteriaId: string) {
    const res = await api.confirmCriterionRepair(
      activeCaseId.value,
      criteriaId,
      activeCriteriaFile.value || undefined,
    )
    const data = res.data as Record<string, unknown> | null | undefined
    alignment.value = normalizeAlignment(data?.alignment)
    const reportNames = Array.isArray(data?.report_names) ? data.report_names.map(String) : []
    for (const reportName of reportNames) {
      await autoGradeReport(reportName, {
        criteriaIds: [criteriaId],
        reason: 'repair',
      })
    }
    await loadAlignment(activeCaseId.value)
    return data
  }

  function normalizeAutoGrade(payload: unknown): AutoGradeResponse {
    const obj =
      payload && typeof payload === 'object'
        ? (payload as AutoGradeResponse & Record<string, unknown>)
        : {}
    return {
      result: (obj.result as GradingResult | null | undefined) ?? null,
      grades: Array.isArray(obj.grades) ? obj.grades : [],
      abstentions: Array.isArray(obj.abstentions) ? obj.abstentions : [],
      failures: Array.isArray(obj.failures) ? obj.failures : [],
    }
  }

  function normalizeCriteriaIds(criteriaIds?: string[]) {
    const ids = (criteriaIds || [])
      .map((item) => String(item))
      .filter((item) => Boolean(item && item !== UNINDUCED_CRITERIA_ID))
    return Array.from(new Set(ids)).sort()
  }

  function autoGradeQueueIsActive() {
    return (
      autoGradeBatchStarted.value &&
      !activeAutoGradeBatch.value &&
      mode.value === 'single' &&
      Boolean(activeCaseId.value && activeCriteriaFile.value)
    )
  }

  function autoGradeJobIsCurrent(job: AutoGradeQueueJob) {
    return (
      autoGradeQueueIsActive() &&
      job.caseId === activeCaseId.value &&
      job.criteriaFileName === activeCriteriaFile.value
    )
  }

  function getReadyCriteriaIds() {
    return alignment.value.readiness
      .filter((item) => isCriterionModelReady(item.criteria_id))
      .map((item) => String(item.criteria_id))
  }

  function autoGradeJobKey(
    job: Pick<AutoGradeQueueJob, 'caseId' | 'criteriaFileName' | 'reportId' | 'criteriaIds'>,
  ) {
    const criteriaKey = normalizeCriteriaIds(job.criteriaIds).join(',') || '*'
    return [job.caseId, job.criteriaFileName || '', job.reportId, criteriaKey].join('::')
  }

  function upsertAutoGradeJobStatus(
    reportId: string,
    criteriaId: string,
    status: AutoGradeJobStatus,
    details: Partial<AutoGradeJobStatusItem> = {},
  ) {
    const now = new Date().toISOString()
    const job: AutoGradeJobStatusItem = {
      ...details,
      job_id:
        details.job_id ||
        [activeCaseId.value, activeCriteriaFile.value || '', reportId, criteriaId].join('::'),
      case_id: activeCaseId.value,
      report_name: reportId,
      criteria_id: String(criteriaId),
      criterion_id: String(criteriaId),
      criteria_file_name: activeCriteriaFile.value || undefined,
      status,
      updated_at: now,
    }
    const index = alignment.value.auto_grade_jobs.findIndex(
      (item) =>
        item.report_name === reportId &&
        String(item.criteria_id) === String(criteriaId) &&
        (item.criteria_file_name || '') === (activeCriteriaFile.value || ''),
    )
    if (index >= 0) {
      alignment.value.auto_grade_jobs.splice(index, 1, {
        ...alignment.value.auto_grade_jobs[index],
        ...job,
      })
    } else {
      alignment.value.auto_grade_jobs.push(job)
    }
  }

  function markQueuedAutoGradeJob(job: AutoGradeQueueJob, status: AutoGradeJobStatus) {
    const criteriaIds = normalizeCriteriaIds(job.criteriaIds).length
      ? normalizeCriteriaIds(job.criteriaIds)
      : getReadyCriteriaIds()
    const now = new Date().toISOString()
    criteriaIds.forEach((criteriaId) =>
      upsertAutoGradeJobStatus(job.reportId, criteriaId, status, {
        reason: job.reason,
        queued_at: status === 'pending' ? now : undefined,
        started_at: status === 'running' ? now : undefined,
      }),
    )
  }

  async function enqueueAutoGradeReports(
    reportIds: string[],
    opts: {
      criteriaIds?: string[]
      reason: AutoGradeQueueReason
      priority: number
    },
  ) {
    const criteriaIds = normalizeCriteriaIds(opts.criteriaIds)
    if (!autoGradeQueueIsActive()) return
    if (
      !criteriaIds.length &&
      !alignment.value.readiness.some((item) => isCriterionModelReady(item.criteria_id))
    )
      return
    reportIds.forEach((reportId) => {
      const report = reports.value.find((item) => item.id === reportId)
      if (
        !report ||
        report.conversion_status !== 'finished' ||
        autoGradeStatusByReport.value[reportId] === 'running'
      )
        return
      const queuedForReport = autoGradeQueue.find(
        (item) =>
          item.caseId === activeCaseId.value &&
          item.criteriaFileName === activeCriteriaFile.value &&
          item.reportId === reportId &&
          ['pregrade', 'backfill', 'benchmark'].includes(item.reason) &&
          ['pregrade', 'backfill', 'benchmark'].includes(opts.reason),
      )
      if (queuedForReport) {
        const previousKey = autoGradeJobKey(queuedForReport)
        const queuedCriteriaIds = normalizeCriteriaIds(queuedForReport.criteriaIds)
        queuedForReport.criteriaIds =
          !queuedCriteriaIds.length || !criteriaIds.length
            ? undefined
            : Array.from(new Set([...queuedCriteriaIds, ...criteriaIds]))
        if (queuedForReport.reason === 'pregrade' || opts.reason === 'pregrade') {
          queuedForReport.reason = 'pregrade'
        }
        queuedForReport.priority = Math.max(queuedForReport.priority, opts.priority)
        queuedAutoGradeKeys.delete(previousKey)
        queuedAutoGradeKeys.add(autoGradeJobKey(queuedForReport))
        markQueuedAutoGradeJob(queuedForReport, 'pending')
        return
      }
      const job: AutoGradeQueueJob = {
        caseId: activeCaseId.value,
        criteriaFileName: activeCriteriaFile.value,
        reportId,
        criteriaIds: criteriaIds.length ? criteriaIds : undefined,
        priority: opts.priority,
        reason: opts.reason,
      }
      const key = autoGradeJobKey(job)
      if (queuedAutoGradeKeys.has(key) || completedAutoGradeKeys.has(key)) return
      queuedAutoGradeKeys.add(key)
      autoGradeQueue.push(job)
      markQueuedAutoGradeJob(job, 'pending')
    })
    autoGradeQueue.sort((a, b) => b.priority - a.priority)
    await processAutoGradeQueue()
  }

  async function processAutoGradeQueue() {
    if (autoGradeQueueRunning.value) return
    autoGradeQueueRunning.value = true
    try {
      while (autoGradeQueue.length) {
        const job = autoGradeQueue.shift()
        if (!job) continue
        const key = autoGradeJobKey(job)
        if (!autoGradeJobIsCurrent(job)) {
          queuedAutoGradeKeys.delete(key)
          continue
        }
        if (['backfill', 'benchmark'].includes(job.reason)) {
          const requested = normalizeCriteriaIds(job.criteriaIds)
          const allowed = requested.filter(isCriterionFullBatchAllowed)
          if (!allowed.length) {
            queuedAutoGradeKeys.delete(key)
            continue
          }
          job.criteriaIds = allowed
        }
        markQueuedAutoGradeJob(job, 'running')
        const result = await autoGradeReport(job.reportId, {
          criteriaIds: job.criteriaIds,
          reason: job.reason,
        })
        // Keep the key reserved while autoGradeReport refreshes alignment. Otherwise
        // the refresh can enqueue the same newly-reliable backfill job a second time.
        queuedAutoGradeKeys.delete(key)
        if (result) completedAutoGradeKeys.add(key)
      }
    } finally {
      autoGradeQueueRunning.value = false
    }
  }

  function clearCompletedAutoGradeJobs(criteriaIds?: string[]) {
    const normalizedIds = normalizeCriteriaIds(criteriaIds)
    const activePrefix = `${activeCaseId.value}::${activeCriteriaFile.value || ''}::`
    Array.from(completedAutoGradeKeys).forEach((key) => {
      if (!key.startsWith(activePrefix)) return
      const criteriaKey = key.split('::')[3] || '*'
      if (!normalizedIds.length || criteriaKey === '*') {
        completedAutoGradeKeys.delete(key)
        return
      }
      const jobCriteriaIds = criteriaKey.split(',')
      if (normalizedIds.some((id) => jobCriteriaIds.includes(id))) {
        completedAutoGradeKeys.delete(key)
      }
    })
  }

  function queuePreGradesAfter(reportId = activeReportId.value) {
    if (!reportId) return
    const currentIndex = reports.value.findIndex((item) => item.id === reportId)
    if (currentIndex < 0) return
    const nextReportIds = reports.value
      .slice(currentIndex + 1)
      .filter(
        (item) =>
          item.conversion_status === 'finished' &&
          autoGradeStatusByReport.value[item.id] !== 'running',
      )
      .slice(0, PRE_GRADE_LOOKAHEAD_COUNT)
      .map((item) => item.id)
    enqueueAutoGradeReports(nextReportIds, {
      reason: 'pregrade',
      priority: 10,
    })
  }

  function queuePreGradesFrom(reportId = activeReportId.value) {
    if (!reportId) return
    const currentIndex = reports.value.findIndex((item) => item.id === reportId)
    if (currentIndex < 0) return
    const existingCriteriaIds = new Set(
      (gradingResults.value[reportId]?.criteria || []).map((item) => String(item.criteria_id)),
    )
    const missingReadyCriteriaIds = getReadyCriteriaIds().filter(
      (criteriaId) => !existingCriteriaIds.has(criteriaId),
    )
    if (missingReadyCriteriaIds.length) {
      enqueueAutoGradeReports([reportId], {
        criteriaIds: missingReadyCriteriaIds,
        reason: 'pregrade',
        priority: 80,
      })
    }
    queuePreGradesAfter(reportId)
  }

  function queueBackfillBefore(reportId: string, criteriaIds: string[]) {
    const currentIndex = reports.value.findIndex((item) => item.id === reportId)
    const source = currentIndex >= 0 ? reports.value.slice(0, currentIndex) : reports.value
    enqueueAutoGradeReports(
      source.map((item) => item.id),
      { criteriaIds, reason: 'pregrade', priority: 80 },
    )
  }

  async function autoGradeReport(
    reportId: string,
    opts: { criteriaIds?: string[]; reason?: AutoGradeQueueReason } = {},
  ) {
    if (!reportId || autoGradeStatusByReport.value[reportId] === 'running') return null
    const requestedCriteria = normalizeCriteriaIds(opts.criteriaIds)
    const criteriaIds = requestedCriteria.length ? requestedCriteria : getReadyCriteriaIds()
    if (criteriaIds.length === 0) return null
    autoGradeStatusByReport.value[reportId] = 'running'
    try {
      const res = await api.autoGradeCaseReport(activeCaseId.value, reportId, {
        criteria_file_name: activeCriteriaFile.value || undefined,
        criteria_ids: criteriaIds,
        reason: opts.reason,
      })
      const normalized = normalizeAutoGrade(res.data)
      if (normalized.result) {
        const result = normalizeResult(normalized.result)
        if (result) {
          applyReportResult(reportId, mergeAutoGradeResult(reportId, result))
        }
      } else if (normalized.grades?.length) {
        const previous = gradingResults.value[reportId]
        if (previous) {
          const byCriteria = new Map(
            previous.criteria.map((item) => [String(item.criteria_id), item]),
          )
          normalized.grades.forEach((item) => {
            const criteriaId = String(item.criteria_id)
            const existing = byCriteria.get(criteriaId)
            if (isTeacherGrade(existing) && isModelGrade(item)) return
            byCriteria.set(criteriaId, item)
          })
          const result = normalizeResult({
            ...previous,
            criteria: Array.from(byCriteria.values()),
          })
          if (result) {
            applyReportResult(reportId, result)
          }
        }
      }
      abstentionsByReport.value[reportId] = [
        ...(normalized.abstentions || []),
        ...(normalized.failures || []),
      ]
      criteriaIds.forEach((criteriaId) => {
        const abstention = normalized.abstentions?.find(
          (item) => String(item.criteria_id) === String(criteriaId),
        )
        const failure = normalized.failures?.find(
          (item) => String(item.criteria_id) === String(criteriaId),
        )
        if (failure) {
          upsertAutoGradeJobStatus(reportId, criteriaId, 'failed', {
            reason: failure.reason,
            error: failure.error,
            completed_at: new Date().toISOString(),
          })
        } else if (abstention) {
          upsertAutoGradeJobStatus(reportId, criteriaId, 'abstained', {
            reason: abstention.reason,
            decision: abstention.admission || abstention.decision,
            completed_at: new Date().toISOString(),
          })
        } else {
          upsertAutoGradeJobStatus(reportId, criteriaId, 'done', {
            reason: 'model_grade_recorded',
            completed_at: new Date().toISOString(),
          })
        }
      })
      autoGradeStatusByReport.value[reportId] = 'done'
      await loadAlignment(activeCaseId.value)
      return normalized
    } catch (err) {
      autoGradeStatusByReport.value[reportId] = 'failed'
      criteriaIds.forEach((criteriaId) =>
        upsertAutoGradeJobStatus(reportId, criteriaId, 'failed', {
          error: err instanceof Error ? err.message : String(err),
          completed_at: new Date().toISOString(),
        }),
      )
      console.warn('[auto-grade] job failed', {
        err,
        caseId: activeCaseId.value,
        reportId,
        reason: opts.reason,
      })
      return null
    }
  }

  async function recoverAutoGradesForCase() {
    if (!LEGACY_ADAPTIVE_SCHEDULER_ENABLED) return
    const authorizedCriteriaIds = new Set(
      alignment.value.criterion_authorizations
        .filter(
          (item) =>
            item.state === 'batch_authorized' &&
            belongsToActiveCriteriaFile(item.criteria_file_name),
        )
        .map((item) => String(item.criterion_id)),
    )
    const reliableCriteriaIds = reliableConsistencyCriteriaIds()
    const recoverableCriteriaIds = new Set([
      ...authorizedCriteriaIds,
      ...reliableCriteriaIds,
      ...getReadyCriteriaIds(),
    ])
    const groupedByReport = new Map<
      string,
      { criteriaIds: Set<string>; hasSelectiveCriterion: boolean }
    >()
    recoverableCriteriaIds.forEach((criterionId) => {
      const summary = getCriterionGlobalSummary(criterionId)
      const eligibleReports = summary?.eligible_report_names || []
      const fullBatch =
        authorizedCriteriaIds.has(criterionId) || reliableCriteriaIds.has(criterionId)
      const reportNames = fullBatch
        ? eligibleReports
        : eligibleReports.slice(0, PRE_GRADE_LOOKAHEAD_COUNT + 1)
      reportNames.forEach((reportId) => {
        const grouped = groupedByReport.get(reportId) || {
          criteriaIds: new Set<string>(),
          hasSelectiveCriterion: false,
        }
        grouped.criteriaIds.add(criterionId)
        grouped.hasSelectiveCriterion ||= !fullBatch
        groupedByReport.set(reportId, grouped)
      })
    })

    // Report-centric jobs keep the visible report complete. The former
    // criterion-centric loop graded criterion 1 on reports 1–3 before criterion
    // 2 on report 1, so the first report appeared to stall.
    const orderedReportIds = reports.value
      .map((item) => item.id)
      .filter((reportId) => groupedByReport.has(reportId))
    const activeIndex = orderedReportIds.indexOf(activeReportId.value)
    if (activeIndex > 0) {
      orderedReportIds.unshift(...orderedReportIds.splice(activeIndex, 1))
    }
    orderedReportIds.forEach((reportId) => {
      const grouped = groupedByReport.get(reportId)
      if (!grouped) return
      const isActiveReport = reportId === activeReportId.value
      enqueueAutoGradeReports([reportId], {
        criteriaIds: Array.from(grouped.criteriaIds),
        reason: grouped.hasSelectiveCriterion ? 'pregrade' : 'benchmark',
        priority: isActiveReport ? 90 : grouped.hasSelectiveCriterion ? 30 : 70,
      })
    })
  }

  async function startBatchAutoGrading() {
    if (
      autoGradeBatchStarted.value ||
      !activeCaseId.value ||
      !activeCriteriaFile.value ||
      !activeReportId.value ||
      !criteriaSchema.value?.criteria.length ||
      !reports.value.length
    ) {
      return false
    }

    autoGradeBatchStarted.value = true
    try {
      const res = await api.createAutoGradeBatch(activeCaseId.value, {
        criteria_file_name: activeCriteriaFile.value,
        report_names: reports.value.map((report) => report.id),
        start_report_name: activeReportId.value,
      })
      return requireAutoGradeBatch(res.data?.batch)
    } catch (err) {
      autoGradeBatchStarted.value = false
      throw err
    }
  }

  async function confirmAutoGradeBatch(batchId: string) {
    const res = await api.confirmAutoGradeBatch(activeCaseId.value, batchId)
    return requireAutoGradeBatch(res.data?.batch)
  }

  async function cancelAutoGradeBatch(batchId: string) {
    const res = await api.cancelAutoGradeBatch(activeCaseId.value, batchId)
    return requireAutoGradeBatch(res.data?.batch)
  }

  async function refreshAutoGradeBatch(batchId: string) {
    const res = await api.getAutoGradeBatch(activeCaseId.value, batchId)
    const batch = requireAutoGradeBatch(res.data?.batch)

    let projectionChanged = false
    for (const reportName of batch.report_names) {
      const targets = batch.targets.filter((target) => target.report_name === reportName)
      if (targets.some((target) => target.status === 'running')) {
        autoGradeStatusByReport.value[reportName] = 'running'
      }
      const terminalTargets = targets.filter((target) =>
        TERMINAL_AUTO_GRADE_TARGET_STATUSES.has(target.status),
      )
      const reportComplete = Boolean(targets.length && terminalTargets.length === targets.length)
      if (reportComplete && targets.every((target) => target.status === 'cancelled')) {
        autoGradeStatusByReport.value[reportName] = 'cancelled'
        continue
      }
      if (reportComplete) {
        autoGradeStatusByReport.value[reportName] = targets.every(
          (target) => target.status === 'failed' || target.status === 'stale',
        )
          ? 'failed'
          : 'done'
      }
      const newTerminalTargets = terminalTargets.filter(
        (target) =>
          !refreshedAutoGradeBatchTargets.has(
            `${batch.batch_id}::${reportName}::${target.criterion_id}`,
          ),
      )
      if (!newTerminalTargets.length) continue
      // Each target becomes terminal only after its partial report projection is
      // persisted, so completed cells can appear without waiting for the row.
      await reloadReportResultForActiveCriteria(reportName)
      newTerminalTargets.forEach((target) => {
        refreshedAutoGradeBatchTargets.add(
          `${batch.batch_id}::${reportName}::${target.criterion_id}`,
        )
      })
      projectionChanged = true
    }

    if (projectionChanged) {
      // Global View is built from server-side projections, not gradingResults.
      // Refresh it after newly completed targets so the heatmap fills cell by cell.
      await loadAlignment(activeCaseId.value)
    }
    if (isTerminalAutoGradeBatch(batch.status)) {
      await reloadReportResultsForActiveCriteria()
      if (!projectionChanged) await loadAlignment(activeCaseId.value)
    }
    return batch
  }

  async function loadCase(caseId: string) {
    gradingResults.value = {}
    highlightsByReport.value = {}
    implicitGrades.value = []
    criterionProposals.value = []
    implicitHighlightsByReport.value = {}
    abstentionsByReport.value = {}
    autoGradeStatusByReport.value = {}
    autoGradeQueue.length = 0
    queuedAutoGradeKeys.clear()
    completedAutoGradeKeys.clear()
    refreshedAutoGradeBatchTargets.clear()
    autoGradeBatchStarted.value = false
    globalReportFocus.value = null
    resetGlobalViewState()
    reports.value = []
    activeReportId.value = ''
    activeCriteriaFile.value = ''
    criteriaSchema.value = null
    loadedCriteriaSchema.value = null
    // The server catalog owns the default, including imported case names that
    // happen to match a local demo. Never request an absent hardcoded rubric.
    await loadCriteria()

    await loadAlignment(caseId)
    const reportsRes = await api.listCaseReports(caseId)
    const loadedReports = await Promise.all(
      (reportsRes.data?.items || []).map(async (report) => {
        const detail = await api.getCaseReport(
          caseId,
          report.name,
          activeCriteriaFile.value || undefined,
        )
        const reportItem: ReportItem = {
          id: report.name,
          name: report.name,
          markdown: detail.data?.markdown || '',
          conversion_status: 'finished',
        }
        return {
          report: reportItem,
          result: detail.data?.result ? normalizeResult(detail.data.result as GradingResult) : null,
        }
      }),
    )

    const reportItems = loadedReports.map((item) => item.report)
    reports.value = reportItems
    loadedReports.forEach(({ report, result }) => {
      // Report markdown must be present before evidence anchors are grouped into
      // logical segments. Model pre-grades remain visible until teacher review.
      if (result) applyReportResult(report.id, result)
    })
    activeReportId.value = reportItems[0]?.id || ''
    await loadImplicitGrades(caseId)
    await loadCriterionProposals(caseId)
    // Opening a case does not create new grading authority. It may resume work
    // for criteria whose persisted teacher authorization is already active.
    activeCriteriaId.value = null
  }

  function startNewCriteria(payload?: {
    title?: string
    background?: string
    base_score?: number
    language?: string
  }) {
    criteriaSchema.value = {
      schema_version: 'structured-rubric-v1',
      title: payload?.title || 'New Criteria',
      background: payload?.background || '',
      language: payload?.language || 'en',
      base_score: payload?.base_score ?? 100,
      criteria: [],
    }
    activeCriteriaId.value = null
  }

  function useLoadedCriteria() {
    if (loadedCriteriaSchema.value) {
      criteriaSchema.value = cloneCriteria(loadedCriteriaSchema.value)
      activeCriteriaId.value = null
    }
  }

  async function ensureCriteriaFileForInduction() {
    if (activeCriteriaFile.value) return activeCriteriaFile.value
    if (!criteriaSchema.value) {
      startNewCriteria({ title: 'criteria', base_score: 100, language: 'en' })
    }
    const fileName = toFileName(criteriaSchema.value?.title || 'criteria')
    await saveCriteriaFile(fileName)
    return activeCriteriaFile.value || fileName
  }

  async function saveImplicitGrade(payload: {
    report_id: string
    score: number
    max_score?: number | null
    reason: string
    anchor: HighlightAnchor
    matched_scoring_points?: string[]
    matched_deduction_rules?: string[]
    scoring_point_scores?: ScoringPointScore[]
  }) {
    const criteriaFileName = await ensureCriteriaFileForInduction()
    const res = await api.createImplicitGrade(activeCaseId.value, {
      report_name: payload.report_id,
      anchor: {
        anchor_type: payload.anchor.anchor_type || 'text',
        text: payload.anchor.text,
        occurrence: payload.anchor.occurrence ?? null,
        start_offset: payload.anchor.start_offset ?? null,
        end_offset: payload.anchor.end_offset ?? null,
        image_src: payload.anchor.image_src ?? null,
        image_alt: payload.anchor.image_alt ?? null,
        region: payload.anchor.region ?? null,
      },
      score: payload.score,
      max_score: payload.max_score ?? null,
      notes: payload.reason || null,
      // A missing teacher reason is drafted by the induction model for review;
      // never persist a UI placeholder as if it were a grading reason.
      temporary_label: null,
      matched_scoring_points: payload.matched_scoring_points || [],
      matched_deduction_rules: payload.matched_deduction_rules || [],
      scoring_point_scores: payload.scoring_point_scores || [],
      criteria_file_name: criteriaFileName,
    })
    const item = res.data?.item
    if (item) {
      const normalizedItem = activateImplicitGrade(item)
      await loadCriterionProposals(activeCaseId.value)
      scheduleInductionRefresh(activeCaseId.value)
      return normalizedItem
    }
    return item
  }

  async function saveTeacherHighlight(payload: TeacherHighlightSavePayload) {
    const criteriaId = payload.criteria_id ? String(payload.criteria_id) : null
    const { safeScore, resolvedMax } = resolveTeacherScore(payload)
    if (!criteriaId || criteriaId === UNINDUCED_CRITERIA_ID) {
      return saveImplicitGrade({ ...payload, score: safeScore })
    }
    const completePayload: TeacherHighlightSavePayload = {
      ...payload,
      scoring_point_scores: resolveTeacherPointScores(payload, criteriaId, safeScore),
    }
    const previousResult = snapshotReportResult(payload.report_id)
    const optimistic = addOptimisticTeacherHighlight(completePayload, criteriaId, safeScore)
    removeAutoGradeJobStatus(payload.report_id, criteriaId)
    upsertOptimisticTeacherGrade(completePayload, criteriaId, safeScore, resolvedMax)
    try {
      const res = await api.saveCaseReportGrade(
        activeCaseId.value,
        payload.report_id,
        buildTeacherGradeRequest(completePayload, criteriaId, safeScore),
      )
      const result = normalizeResult(res.data?.result as GradingResult | null) || undefined
      if (result) {
        refreshSavedTeacherHighlight(payload.report_id, criteriaId, result, payload.anchor)
      }
      return null
    } catch (err) {
      removeHighlight(payload.report_id, optimistic.id)
      applyReportResult(payload.report_id, previousResult)
      console.error('[highlight] save failed', {
        err,
        caseId: activeCaseId.value,
        reportId: payload.report_id,
        criteriaId: payload.criteria_id,
        score: safeScore,
        anchor: payload.anchor,
      })
      throw err
    }
  }

  async function retryImplicitGrade(implicitGradeId: string) {
    const res = await api.retryImplicitGrade(activeCaseId.value, implicitGradeId)
    const item = res.data?.item
    if (!item) return null
    const normalizedItem = activateImplicitGrade(item)
    scheduleInductionRefresh(activeCaseId.value)
    return normalizedItem
  }

  async function deleteTeacherHighlight(payload: {
    report_id: string
    highlight_id: string
    criteria_id: string
  }) {
    if (payload.criteria_id === UNINDUCED_CRITERIA_ID) {
      const implicitId = payload.highlight_id.replace(/^implicit-/, '')
      await api.deleteImplicitGrade(activeCaseId.value, implicitId)
      implicitGrades.value = implicitGrades.value.filter(
        (item) => item.implicit_grade_id !== implicitId,
      )
      hydrateImplicitHighlights()
      activeHighlightId.value = null
      activeImplicitGradeId.value = null
      return
    }
    const previousResult = snapshotReportResult(payload.report_id)
    removeHighlight(payload.report_id, payload.highlight_id)
    activeHighlightId.value = null
    try {
      const res = await api.deleteCaseReportGrade(activeCaseId.value, payload.report_id, {
        criteria_id: payload.criteria_id,
        criteria_file_name: activeCriteriaFile.value || undefined,
      })
      const result = normalizeResult(res.data?.result as GradingResult | null)
      if (result) {
        applyReportResult(payload.report_id, result)
      } else {
        removeHighlight(payload.report_id, payload.highlight_id)
      }
      void runTeacherGradeFollowup(payload.report_id, payload.criteria_id)
    } catch (err) {
      applyReportResult(payload.report_id, previousResult)
      console.error('[highlight] delete failed', {
        err,
        caseId: activeCaseId.value,
        reportId: payload.report_id,
        criteriaId: payload.criteria_id,
      })
      throw err
    }
  }

  async function saveGradeDirect(payload: {
    reportId: string
    criteriaId: string
    score: number
    reason: string
    anchorText: string
    targetSection?: string | null
    matchedScoringPoints?: string[]
    matchedDeductionRules?: string[]
    scoringPointScores?: ScoringPointScore[]
  }) {
    const criteriaId = String(payload.criteriaId)
    const safeScore = Number.isFinite(payload.score) ? payload.score : 0
    const criteriaMeta = criteriaSchema.value?.criteria.find(
      (item) => String(item.criteria_id) === criteriaId,
    )
    const resolvedMax = Number.isFinite(criteriaMeta?.max_score ?? NaN)
      ? Number(criteriaMeta?.max_score)
      : Math.max(safeScore, 0)
    const teacherPayload: TeacherHighlightSavePayload = {
      report_id: payload.reportId,
      criteria_id: criteriaId,
      score: safeScore,
      max_score: resolvedMax,
      reason: payload.reason,
      anchor: { text: payload.anchorText },
      target_section: payload.targetSection ?? null,
      matched_scoring_points: payload.matchedScoringPoints || [],
      matched_deduction_rules: payload.matchedDeductionRules || [],
      scoring_point_scores: payload.scoringPointScores || [],
    }
    teacherPayload.scoring_point_scores = resolveTeacherPointScores(
      teacherPayload,
      criteriaId,
      safeScore,
    )
    const previousResult = snapshotReportResult(payload.reportId)
    removeAutoGradeJobStatus(payload.reportId, criteriaId)
    upsertOptimisticTeacherGrade(teacherPayload, criteriaId, safeScore, resolvedMax)
    try {
      const res = await api.saveCaseReportGrade(
        activeCaseId.value,
        payload.reportId,
        buildTeacherGradeRequest(teacherPayload, criteriaId, safeScore),
      )
      const result = normalizeResult(res.data?.result as GradingResult | null) || null
      if (result) {
        applyReportResult(payload.reportId, result)
        removeAutoGradeJobStatus(payload.reportId, criteriaId)
        activateLatestTeacherHighlight(payload.reportId, criteriaId, {
          text: payload.anchorText,
        })
        void runTeacherGradeFollowup(payload.reportId, criteriaId)
      }
      return result
    } catch (err) {
      applyReportResult(payload.reportId, previousResult)
      console.error('[grade] save failed', {
        err,
        caseId: activeCaseId.value,
        reportId: payload.reportId,
        criteriaId,
      })
      throw err
    }
  }

  async function acceptModelGrade(reportId: string, criteriaId: string) {
    const normalizedCriteriaId = String(criteriaId)
    const previousResult = snapshotReportResult(reportId)
    try {
      const res = await api.acceptCaseReportModelGrade(activeCaseId.value, reportId, {
        criteria_id: normalizedCriteriaId,
        criteria_file_name: activeCriteriaFile.value || undefined,
      })
      const result = normalizeResult(res.data?.result as GradingResult | null) || null
      if (result) {
        applyReportResult(reportId, result)
        removeAutoGradeJobStatus(reportId, normalizedCriteriaId)
        await loadAlignment(activeCaseId.value)
      }
      return result
    } catch (err) {
      applyReportResult(reportId, previousResult)
      console.error('[grade] accept model grade failed', {
        err,
        caseId: activeCaseId.value,
        reportId,
        criteriaId: normalizedCriteriaId,
      })
      throw err
    }
  }

  async function confirmCriterionProposal(
    proposal: CriterionProposalItem,
    suggested = proposal.suggested_criterion,
    targetCriterionId: string | null | undefined = undefined,
    options: {
      onPersisted?: (confirmed: CriterionProposalItem | null) => void
    } = {},
  ) {
    let confirmed: CriterionProposalItem | null
    try {
      const res = await api.confirmCriterionProposal(activeCaseId.value, proposal.proposal_id, {
        proposal_version: proposal.proposal_version,
        suggested_criterion: suggested,
        ...(targetCriterionId === undefined ? {} : { target_criterion_id: targetCriterionId }),
        criteria_file_name: proposal.criteria_file_name,
      })
      confirmed = res.data?.item || null
    } catch (err) {
      console.error('[proposal] confirmation failed', err)
      throw err
    }

    // The proposal is durably confirmed once the mutation request succeeds.
    // Let the review UI close at that boundary instead of holding it open for
    // the slower reconciliation reads below.
    options.onPersisted?.(confirmed)

    const nextCriteriaId = confirmed?.suggested_criterion?.criteria_id || suggested.criteria_id
    const reportId = activeReportId.value
    try {
      await loadCriteria(proposal.criteria_file_name)
      await reloadReportResultForActiveCriteria(reportId)
      await Promise.all([
        loadImplicitGrades(activeCaseId.value),
        loadCriterionProposals(activeCaseId.value),
      ])
      activeCriteriaId.value = nextCriteriaId || null
      activeImplicitGradeId.value = null
      activeHighlightId.value = null
      if (nextCriteriaId) {
        activateLatestTeacherHighlight(activeReportId.value, nextCriteriaId)
      }
      if (nextCriteriaId && confirmed?.proposal_type === 'create_criterion') {
        void runPostGradeStandardReview(reportId, nextCriteriaId, {
          backfillPrevious: true,
        })
      }
    } catch (err) {
      console.warn('[proposal] confirmed, but follow-up refresh failed', err)
      alignmentError.value =
        'The scoring standard was updated, but the latest view could not be refreshed. Reload the page to reconcile it.'
    }
    return confirmed
  }

  async function rejectCriterionProposal(proposal: CriterionProposalItem, reason?: string) {
    const res = await api.rejectCriterionProposal(activeCaseId.value, proposal.proposal_id, {
      proposal_version: proposal.proposal_version,
      reason,
    })
    await loadImplicitGrades(activeCaseId.value)
    await loadCriterionProposals(activeCaseId.value)
    if (proposal.target_criterion_id) {
      void auditAndEvaluateAlignment(proposal.target_criterion_id).catch((err) => {
        console.warn('[proposal] rejection follow-up alignment refresh failed', err)
      })
    }
    return res.data?.item || null
  }

  return {
    reports,
    cases,
    activeCaseId,
    criteriaFiles,
    activeCriteriaFile,
    loadedCriteriaSchema,
    criteriaSchema,
    gradingResults,
    highlightsByReport,
    implicitGrades,
    criterionProposals,
    implicitHighlightsByReport,
    alignment,
    alignmentLoading,
    alignmentError,
    abstentionsByReport,
    autoGradeStatusByReport,
    autoGradeBatchStarted,
    activeReportId,
    highlightFocusToken,
    evidenceAdjustmentToken,
    activeCriteriaId,
    activeImplicitGradeId,
    activeProposalId,
    activeHighlightId,
    mode,
    globalSelectedCriterionId,
    globalReportSortDirection,
    globalFocusedReportId,
    globalZoomByCase,
    globalReportFocus,
    descriptionDiff,
    diffVisible,
    activeReport,
    activeCriteria,
    activeImplicitGrade,
    activeHighlights,
    activeCriteriaImplicitGrades,
    currentReportImplicitGrades,
    currentReportPendingProposals,
    reportHighlights,
    pendingProposals,
    currentReportScore,
    readinessByCriteria,
    openConflicts,
    conflictCount,
    llmEnabled,
    activeAutoGradeBatch,
    isAcceptedModelGrade,
    canAcceptModelGrade,
    setActiveReport,
    setActiveCase,
    getCaseDefaultCriteria,
    isExperimentResetAvailable,
    setActiveCriteria,
    setActiveImplicitGrade,
    setActiveProposal,
    setActiveHighlight,
    focusHighlight,
    requestEvidenceAdjustment,
    toggleMode,
    setGlobalSelectedCriterion,
    resetGlobalCriterionDrilldown,
    setGlobalFocusedReport,
    setGlobalZoom,
    inspectInReportView,
    focusGlobalPoint,
    addTeacherHighlight,
    removeHighlight,
    getCriterionReadiness,
    isCriterionModelReady,
    belongsToActiveCriteriaFile,
    implicitGradeBelongsToActiveCriteriaFile,
    getCriterionConflicts,
    getCriterionAuthorization,
    getCriterionConsistency,
    getReviewAssignment,
    isCriterionFullBatchAllowed,
    getCriterionGlobalSummary,
    getReportAbstentions,
    getCriterionAbstention,
    getCriterionAutoGradeJob,
    jumpToConflictEvent,
    openDiff,
    closeDiff,
    hydrateHighlights,
    loadCases,
    resetCurrentExperiment,
    loadCriteriaList,
    loadCriteria,
    changeCriteriaFile,
    saveCriteriaFile,
    previewCriteriaCategories,
    applyCriteriaCategories,
    deleteCriteriaFile,
    deleteCriteriaItem,
    loadAlignment,
    loadImplicitGrades,
    loadCriterionProposals,
    evaluateAlignment,
    auditAlignment,
    auditAndEvaluateAlignment,
    resolveConflict,
    authorizeCriterion,
    confirmCriterionRepair,
    autoGradeReport,
    startBatchAutoGrading,
    confirmAutoGradeBatch,
    cancelAutoGradeBatch,
    refreshAutoGradeBatch,
    recoverAutoGradesForCase,
    loadCase,
    startNewCriteria,
    useLoadedCriteria,
    saveTeacherHighlight,
    saveImplicitGrade,
    retryImplicitGrade,
    deleteTeacherHighlight,
    saveGradeDirect,
    acceptModelGrade,
    confirmCriterionProposal,
    rejectCriterionProposal,
    UNINDUCED_CRITERIA_ID,
  }
})
