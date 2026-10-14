<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useGradingStore } from '../stores/grading'
import ConflictReviewPanel from './ConflictReviewPanel.vue'
import CriteriaEditorModal from './CriteriaEditorModal.vue'
import CriteriaCategoryUpdateModal from './CriteriaCategoryUpdateModal.vue'
import ScoringPointsDiff from './ScoringPointsDiff.vue'
import StructuredRubricEditor from './StructuredRubricEditor.vue'
import ReportAssessmentRow from './report-view/ReportAssessmentRow.vue'
import ReportScoreProfile from './report-view/ReportScoreProfile.vue'
import type { ReportGradeDraft, ReportRubricDraft } from './report-view/reportAssessmentTypes'
import { translate as t } from '../i18n'
import { isAutoGradeMiss } from './criteriaCardPresentation'
const $t = t
import {
  normalizeScoringAggregation,
  validateScoringAggregation,
  validateScoringPoints,
  validateStructuredScoreBudget,
} from '../utils/scoringPoints'
import type {
  AlignmentConflict,
  AutoGradeAbstention,
  AutoGradeJobStatusItem,
  CriteriaItem,
  CriteriaCategoryPreview,
  CriterionProposalItem,
  GradingItem,
  HighlightItem,
  ImplicitGradeItem,
} from '../types/schema'

const store = useGradingStore()
const expandedSections = ref<Record<string, boolean>>({})
const selectedCriteriaSource = ref<string>('')
const showCreateModal = ref(false)
const editorOpen = ref(false)
const editorInitialTab = ref<'report' | 'criteria'>('report')
const createForm = ref({
  title: 'New Criteria',
  background: '',
  base_score: 100,
  language: 'en',
  customLanguage: '',
})
const languageOptions = ['en', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'ru']
const creating = ref(false)
const deletingCriteriaFile = ref(false)
const deletingCriteriaId = ref<string | null>(null)
const categoryPreview = ref<CriteriaCategoryPreview | null>(null)
const categoryPreviewLoading = ref(false)
const categorySaving = ref(false)
const categoryError = ref('')
const batchStarting = ref(false)
const autoGradeMessage = ref('')
const autoGradeError = ref('')
const proposalModalOpen = ref(false)
const proposalSaving = ref(false)
const proposalError = ref('')
const proposalDraft = ref<CriteriaItem | null>(null)
const activeProposal = ref<CriterionProposalItem | null>(null)
const proposalTargetCriterionId = ref<string | null>(null)
const proposalEditing = ref(false)
const authorizationActionId = ref<string | null>(null)
const modelAcceptanceActionId = ref<string | null>(null)
const inlineGradeSavingId = ref<string | null>(null)
const inlineRubricSavingId = ref<string | null>(null)
const inlineActionErrors = ref<Record<string, string>>({})
const retryingImplicitGradeId = ref<string | null>(null)
const profileProposalSavingId = ref<string | null>(null)
const profileProposalError = ref('')
const criterionCardElements = new Map<string, HTMLElement>()
const managedExperimentCriteria = computed(
  () => store.getCaseDefaultCriteria(store.activeCaseId) !== undefined,
)

const criteriaFileLabel = (file: string) => {
  if (file === store.activeCriteriaFile && store.loadedCriteriaSchema?.title) {
    return store.loadedCriteriaSchema.title
  }
  if (/^import_[a-f0-9]{32}\.json$/i.test(file)) return `${store.activeCaseId} — ${$t('Criteria')}`
  return file.replace(/^[a-f0-9]{32}_/i, '').replace(/\.json$/i, '')
}

const criteriaSourceOptions = computed(() => {
  const files = store.criteriaFiles.map((file) => ({
    label: criteriaFileLabel(file),
    value: file,
  }))
  return managedExperimentCriteria.value
    ? files
    : [{ label: $t('Select...'), value: '' }, ...files]
})

const groupedCriteria = computed(() => {
  const items = store.criteriaSchema?.criteria ?? []
  const groups: Record<string, typeof items> = {}
  items.forEach((item) => {
    const key = item.section?.trim() || $t('Category pending update')
    if (!groups[key]) groups[key] = []
    groups[key].push(item)
  })
  return Object.entries(groups)
})

const autoGradeRunning = computed(
  () => batchStarting.value || store.autoGradeStatusByReport[store.activeReportId] === 'running',
)
const allReportsFullyGraded = computed(() => {
  const summaries = store.alignment.global_report_summaries ?? []
  return (
    summaries.length === store.reports.length &&
    summaries.length > 0 &&
    summaries.every(
      (report) =>
        report.total_criteria_count > 0 &&
        report.graded_criteria_count === report.total_criteria_count,
    )
  )
})
const canStartAutoGrade = computed(
  () =>
    Boolean(store.activeReportId) &&
    Boolean(store.activeCriteriaFile) &&
    Boolean(store.criteriaSchema?.criteria.length) &&
    !allReportsFullyGraded.value &&
    !store.autoGradeBatchStarted &&
    !autoGradeRunning.value,
)
const autoGradeButtonTitle = computed(() => {
  if (!store.reports.length) return $t('Load a report batch before starting auto-grading')
  if (!store.activeCriteriaFile || !store.criteriaSchema?.criteria.length) {
    return $t('Select a criteria file before starting auto-grading')
  }
  if (allReportsFullyGraded.value) return $t('All reports are already fully graded')
  if (store.autoGradeBatchStarted) return $t('Batch auto-grading is active')
  return $t('Start auto-grading for all {count} reports', {
    count: store.reports.length,
  })
})
watch(
  () => store.activeAutoGradeBatch?.status,
  (status) => {
    if (status === 'completed') {
      autoGradeMessage.value = $t(
        'Batch auto-grading completed. All scoring targets were processed.',
      )
      autoGradeError.value = ''
    } else if (status === 'completed_with_issues') {
      autoGradeMessage.value =
        'Batch completed. Items needing teacher review remain visible in the criteria list.'
      autoGradeError.value = ''
    } else if (status === 'failed') {
      autoGradeError.value = $t('Batch auto-grading failed. Check the criteria status below.')
    } else if (status === 'superseded') {
      autoGradeMessage.value =
        'The rubric changed. Old Card results were invalidated and a full Card re-evaluation was started.'
      autoGradeError.value = ''
    } else if (status === 'cancelled') {
      autoGradeMessage.value = $t('Batch auto-grading stopped. Completed report results were kept.')
      autoGradeError.value = ''
    }
  },
)

const gradingMap = computed(() => {
  const result = store.gradingResults[store.activeReportId]
  if (!result) return new Map<string, GradingItem>()
  return new Map(result.criteria.map((item) => [String(item.criteria_id), item]))
})

const conflictsByCriteria = computed(() => {
  const byCriteria = new Map<string, AlignmentConflict[]>()
  store.openConflicts.forEach((item) => {
    const criteriaId = String(item.criteria_id)
    const conflicts = byCriteria.get(criteriaId) || []
    conflicts.push(item)
    byCriteria.set(criteriaId, conflicts)
  })
  return byCriteria
})

const abstentionByCriteria = computed(() => {
  const byCriteria = new Map<string, AutoGradeAbstention>()
  store.getReportAbstentions(store.activeReportId).forEach((item) => {
    byCriteria.set(String(item.criteria_id), item)
  })
  return byCriteria
})

const autoGradeJobByCriteria = computed(() => {
  const byCriteria = new Map<string, AutoGradeJobStatusItem>()
  store.alignment.auto_grade_jobs.forEach((item) => {
    if (item.report_name !== store.activeReportId) return
    if (store.activeCriteriaFile && item.criteria_file_name !== store.activeCriteriaFile) return
    byCriteria.set(String(item.criteria_id), item)
  })
  return byCriteria
})

const reviewJobByCriteria = computed(() => {
  const byCriteria = new Map<string, AutoGradeJobStatusItem>()
  store.alignment.audit_jobs?.forEach((item) => {
    if (store.activeCriteriaFile && item.criteria_file_name !== store.activeCriteriaFile) return
    byCriteria.set(String(item.criteria_id), item)
  })
  return byCriteria
})

const reviewCriteriaIds = computed(() =>
  (store.criteriaSchema?.criteria || [])
    .filter((criterion) => {
      const criteriaId = String(criterion.criteria_id)
      const assignment = store.getReviewAssignment(store.activeReportId, criteriaId)
      return Boolean(
        abstentionByCriteria.value.has(criteriaId) ||
        (assignment?.review_required && !assignment.reviewed_at),
      )
    })
    .map((criterion) => String(criterion.criteria_id)),
)

const visibleImplicitGrades = computed(() => store.currentReportImplicitGrades)

const pendingProposalsByCriteria = computed(() => {
  const byCriteria = new Map<string, CriterionProposalItem[]>()
  store.pendingProposals.forEach((proposal) => {
    if (!proposal.target_criterion_id) return
    const criteriaId = String(proposal.target_criterion_id)
    const items = byCriteria.get(criteriaId) || []
    items.push(proposal)
    byCriteria.set(criteriaId, items)
  })
  return byCriteria
})

const proposalSourceCount = (proposal: CriterionProposalItem) =>
  (proposal.source_implicit_grade_ids?.length || 0) + (proposal.source_event_ids?.length || 0)
const reviewedProposalTarget = computed(() => {
  if (proposalTargetCriterionId.value === null) return null
  return (
    store.criteriaSchema?.criteria.find(
      (item) => String(item.criteria_id) === proposalTargetCriterionId.value,
    ) || null
  )
})
const proposalSourceReasons = computed(() =>
  (activeProposal.value?.source_grade_assignments || []).map((assignment) => {
    const source = store.implicitGrades.find(
      (item) => item.implicit_grade_id === assignment.implicit_grade_id,
    )
    return {
      assignment,
      reportName: source?.report_name || assignment.implicit_grade_id,
      score: source?.score,
      teacherReason: source?.notes?.trim() || '',
    }
  }),
)

const getGrading = (criteriaId: string) => gradingMap.value.get(String(criteriaId))
const getReadiness = (criteriaId: string) => store.getCriterionReadiness(criteriaId)
const getConflicts = (criteriaId: string) => conflictsByCriteria.value.get(String(criteriaId)) || []
const getAuthorization = (criteriaId: string) => store.getCriterionAuthorization(criteriaId)
const getConsistency = (criteriaId: string) => store.getCriterionConsistency(criteriaId)
const getReviewAssignment = (criteriaId: string) =>
  store.getReviewAssignment(store.activeReportId, criteriaId)
const authorizeCriterion = async (criteriaId: string) => {
  authorizationActionId.value = String(criteriaId)
  try {
    await store.authorizeCriterion(String(criteriaId))
  } catch (err) {
    store.alignmentError = err instanceof Error ? err.message : $t('Batch authorization failed')
  } finally {
    authorizationActionId.value = null
  }
}

const confirmCriterionRepair = async (criteriaId: string) => {
  authorizationActionId.value = String(criteriaId)
  try {
    await store.confirmCriterionRepair(String(criteriaId))
  } catch (err) {
    store.alignmentError = err instanceof Error ? err.message : $t('Repair failed')
  } finally {
    authorizationActionId.value = null
  }
}
const isTeacherGrading = (grading: GradingItem | null | undefined) => {
  const raw = (grading?.graded_by || grading?.grader_type || '').toString().toLowerCase()
  return raw === 'teacher' || raw === 'user'
}
const evidenceSegmentsByCriteria = computed(() => {
  const grouped = new Map<string, HighlightItem[]>()
  store.reportHighlights.forEach((highlight) => {
    if (highlight.source === 'implicit') return
    const criteriaId = String(highlight.criteria_id)
    const segments = grouped.get(criteriaId) || []
    segments.push(highlight)
    grouped.set(criteriaId, segments)
  })

  grouped.forEach((segments, criteriaId) => {
    const expectedSource = isTeacherGrading(gradingMap.value.get(criteriaId)) ? 'teacher' : 'llm'
    const matchingSource = segments.filter((item) => item.source === expectedSource)
    if (matchingSource.length) grouped.set(criteriaId, matchingSource)
  })
  return grouped
})
const getEvidenceSegments = (criteriaId: string) =>
  evidenceSegmentsByCriteria.value.get(String(criteriaId)) || []
const getAbstention = (criteriaId: string) =>
  isTeacherGrading(getGrading(criteriaId))
    ? null
    : abstentionByCriteria.value.get(String(criteriaId)) || null
const getAutoGradeJob = (criteriaId: string) =>
  isTeacherGrading(getGrading(criteriaId))
    ? null
    : autoGradeJobByCriteria.value.get(String(criteriaId)) || null
const getReviewJob = (criteriaId: string) =>
  reviewJobByCriteria.value.get(String(criteriaId)) || null
const getPendingProposals = (criteriaId: string) =>
  pendingProposalsByCriteria.value.get(String(criteriaId)) || []

const missedAutoGradeCriteria = computed(() =>
  (store.criteriaSchema?.criteria || []).filter((item) =>
    isAutoGradeMiss({
      grading: getGrading(item.criteria_id),
      abstention: getAbstention(item.criteria_id),
      autoGradeJob: getAutoGradeJob(item.criteria_id),
    }),
  ),
)

const acceptModelPreGrade = async (criteriaId: string) => {
  if (!store.activeReportId || modelAcceptanceActionId.value) return
  modelAcceptanceActionId.value = String(criteriaId)
  try {
    await store.acceptModelGrade(store.activeReportId, String(criteriaId))
  } catch (err) {
    store.alignmentError =
      err instanceof Error ? err.message : $t('Failed to accept model pre-grade')
  } finally {
    modelAcceptanceActionId.value = null
  }
}

const setInlineActionError = (criteriaId: string, message = '') => {
  inlineActionErrors.value = {
    ...inlineActionErrors.value,
    [String(criteriaId)]: message,
  }
}

const saveInlineGrade = async (draft: ReportGradeDraft) => {
  if (!store.activeReportId || inlineGradeSavingId.value) return
  inlineGradeSavingId.value = draft.criteriaId
  setInlineActionError(draft.criteriaId)
  try {
    await store.saveGradeDirect({
      reportId: store.activeReportId,
      criteriaId: draft.criteriaId,
      score: draft.score,
      reason: draft.reason,
      anchorText: draft.anchorText,
      matchedScoringPoints: draft.matchedScoringPoints,
      matchedDeductionRules: draft.matchedDeductionRules,
      scoringPointScores: draft.scoringPointScores,
    })
  } catch (error) {
    setInlineActionError(
      draft.criteriaId,
      error instanceof Error ? error.message : $t('Failed to save teacher grade'),
    )
  } finally {
    inlineGradeSavingId.value = null
  }
}

const saveInlineRubric = async (draft: ReportRubricDraft) => {
  if (!store.criteriaSchema || inlineRubricSavingId.value) return
  const criterion = JSON.parse(JSON.stringify(draft.criterion)) as CriteriaItem
  criterion.scoring_aggregation = normalizeScoringAggregation(criterion.scoring_aggregation)
  const errors = [
    ...(criterion.section?.trim() ? [] : [$t('Every criterion needs a category')]),
    ...validateScoringPoints(criterion.scoring_points),
    ...validateScoringAggregation(criterion.scoring_aggregation),
    ...validateStructuredScoreBudget(
      criterion.scoring_points,
      Number(criterion.max_score) || 0,
      criterion.scoring_aggregation,
    ),
  ]
  if (errors.length) {
    setInlineActionError(criterion.criteria_id, errors.join('; '))
    return
  }

  const previousSchema = JSON.parse(JSON.stringify(store.criteriaSchema))
  inlineRubricSavingId.value = String(criterion.criteria_id)
  setInlineActionError(criterion.criteria_id)
  store.criteriaSchema = {
    ...store.criteriaSchema,
    criteria: store.criteriaSchema.criteria.map((item) =>
      String(item.criteria_id) === String(criterion.criteria_id) ? criterion : item,
    ),
  }
  try {
    await store.saveCriteriaFile()
    store.setActiveCriteria(String(criterion.criteria_id))
  } catch (error) {
    store.criteriaSchema = previousSchema
    store.setActiveCriteria(String(criterion.criteria_id))
    setInlineActionError(
      criterion.criteria_id,
      error instanceof Error ? error.message : $t('Failed to save scoring standard'),
    )
  } finally {
    inlineRubricSavingId.value = null
  }
}

const onSelectCriteria = (criteriaId: string) => {
  const normalizedCriteriaId = String(criteriaId)
  store.setActiveCriteria(normalizedCriteriaId)
  const evidenceSegments = getEvidenceSegments(normalizedCriteriaId)
  const highlight =
    evidenceSegments.find((item) => item.id === store.activeHighlightId) || evidenceSegments[0]
  store.focusHighlight(highlight?.id || null)
}

const onToggleCriteria = (criteriaId: string) => {
  const normalizedCriteriaId = String(criteriaId)
  if (store.activeCriteriaId !== normalizedCriteriaId) {
    onSelectCriteria(normalizedCriteriaId)
    return
  }
  clearActiveCard()
}

const onSelectEvidenceSegment = (payload: { criteriaId: string; highlightId: string }) => {
  store.setActiveCriteria(payload.criteriaId)
  store.focusHighlight(payload.highlightId)
}

const onAdjustEvidence = (payload: { criteriaId: string; highlightId: string | null }) => {
  store.requestEvidenceAdjustment(payload.criteriaId, payload.highlightId)
}

const clearActiveCard = () => {
  if (!store.activeCriteriaId && !store.activeImplicitGradeId) return
  store.setActiveCriteria(null)
  store.setActiveHighlight(null)
}

const onCriteriaViewClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Element)) return
  if (target.closest('[data-selectable-card], [data-keep-card-selection]')) return
  clearActiveCard()
}

const jumpToNextUngraded = () => {
  const criteria = store.criteriaSchema?.criteria || []
  if (!criteria.length || !missedAutoGradeCriteria.value.length) return
  const missedIds = new Set(missedAutoGradeCriteria.value.map((item) => String(item.criteria_id)))

  const activeIndex = criteria.findIndex(
    (item) => String(item.criteria_id) === String(store.activeCriteriaId || ''),
  )
  for (let offset = 1; offset <= criteria.length; offset += 1) {
    const item = criteria[(Math.max(activeIndex, -1) + offset) % criteria.length]
    if (item && missedIds.has(String(item.criteria_id))) {
      onSelectCriteria(String(item.criteria_id))
      return
    }
  }
}

const setCriterionCardElement = (criteriaId: string, element: unknown) => {
  const id = String(criteriaId)
  if (element instanceof HTMLElement) criterionCardElements.set(id, element)
  else criterionCardElements.delete(id)
}

const scrollActiveCriterionIntoView = async (criteriaId: string | null) => {
  if (!criteriaId || criteriaId === store.UNINDUCED_CRITERIA_ID) return
  const criterion = store.criteriaSchema?.criteria.find(
    (item) => String(item.criteria_id) === String(criteriaId),
  )
  if (!criterion) return

  const section = criterion.section?.trim() || $t('Category pending update')
  if (!isExpanded(section)) expandedSections.value[section] = true
  await nextTick()
  criterionCardElements
    .get(String(criteriaId))
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const selectImplicitGrade = (item: ImplicitGradeItem) => {
  store.setActiveImplicitGrade(item.implicit_grade_id)
  store.setActiveHighlight(`implicit-${item.implicit_grade_id}`)
}

const openCriterionEditor = (criteriaId: string) => {
  if (String(store.activeCriteriaId || '') !== String(criteriaId)) {
    store.setActiveCriteria(String(criteriaId))
  }
  editorInitialTab.value = 'criteria'
  editorOpen.value = true
}

const openImplicitEditor = (item: ImplicitGradeItem) => {
  selectImplicitGrade(item)
  editorInitialTab.value = 'report'
  editorOpen.value = true
}

const retryImplicitGrade = async (item: ImplicitGradeItem) => {
  if (retryingImplicitGradeId.value) return
  retryingImplicitGradeId.value = item.implicit_grade_id
  try {
    await store.retryImplicitGrade(item.implicit_grade_id)
  } catch (err) {
    autoGradeError.value = err instanceof Error ? err.message : $t('Rubric generation retry failed')
  } finally {
    retryingImplicitGradeId.value = null
  }
}

const openProposal = (proposal: CriterionProposalItem) => {
  activeProposal.value = proposal
  proposalDraft.value = JSON.parse(JSON.stringify(proposal.suggested_criterion)) as CriteriaItem
  proposalTargetCriterionId.value = proposal.target_criterion_id
    ? String(proposal.target_criterion_id)
    : null
  proposalEditing.value = false
  proposalError.value = ''
  proposalModalOpen.value = true
}

watch(
  () => store.activeProposalId,
  (proposalId) => {
    if (!proposalId) return
    const proposal = store.pendingProposals.find((item) => item.proposal_id === proposalId)
    if (proposal) openProposal(proposal)
    store.setActiveProposal(null)
  },
  { flush: 'sync' },
)

const closeProposal = () => {
  proposalModalOpen.value = false
  activeProposal.value = null
  proposalDraft.value = null
  proposalTargetCriterionId.value = null
  proposalEditing.value = false
  proposalError.value = ''
}

const prepareProposalDraft = (criterion: CriteriaItem) => {
  const draft = JSON.parse(JSON.stringify(criterion)) as CriteriaItem
  draft.scoring_aggregation = normalizeScoringAggregation(draft.scoring_aggregation)
  const errors = [
    ...(draft.section?.trim() ? [] : [$t('Every criterion needs a category')]),
    ...validateScoringPoints(draft.scoring_points),
    ...validateScoringAggregation(draft.scoring_aggregation),
    ...validateStructuredScoreBudget(
      draft.scoring_points,
      Number(draft.max_score) || 0,
      draft.scoring_aggregation,
    ),
  ]
  return { draft, error: errors.join('; ') }
}

const confirmProposal = async () => {
  if (!activeProposal.value || !proposalDraft.value || proposalSaving.value) return
  const proposal = activeProposal.value
  const prepared = prepareProposalDraft(proposalDraft.value)
  proposalError.value = ''
  if (prepared.error) {
    proposalError.value = prepared.error
    return
  }
  proposalSaving.value = true
  try {
    const originalTargetId = proposal.target_criterion_id
      ? String(proposal.target_criterion_id)
      : null
    const selectedTargetId = proposalTargetCriterionId.value
    await store.confirmCriterionProposal(
      proposal,
      prepared.draft,
      selectedTargetId === originalTargetId ? undefined : selectedTargetId,
      { onPersisted: closeProposal },
    )
  } catch (err) {
    console.error('[proposal] confirmation failed', err)
    proposalError.value = err instanceof Error ? err.message : $t('Failed to confirm rubric update')
  } finally {
    proposalSaving.value = false
  }
}

const confirmProfileProposal = async (proposal: CriterionProposalItem) => {
  if (profileProposalSavingId.value) return
  const prepared = prepareProposalDraft(proposal.suggested_criterion)
  profileProposalError.value = prepared.error
  if (prepared.error) return
  profileProposalSavingId.value = proposal.proposal_id
  try {
    await store.confirmCriterionProposal(proposal, prepared.draft)
  } catch (err) {
    console.error('[score-profile] proposal confirmation failed', err)
    profileProposalError.value =
      err instanceof Error ? err.message : $t('Failed to confirm rubric update')
  } finally {
    profileProposalSavingId.value = null
  }
}

const rejectProposal = async () => {
  if (!activeProposal.value || proposalSaving.value) return
  proposalSaving.value = true
  proposalError.value = ''
  try {
    await store.rejectCriterionProposal(
      activeProposal.value,
      'Teacher rejected the induced criterion proposal.',
    )
    closeProposal()
  } catch (err) {
    proposalError.value = err instanceof Error ? err.message : $t('Proposal rejection failed')
  } finally {
    proposalSaving.value = false
  }
}

const isExpanded = (section: string) => expandedSections.value[section] ?? true
const toggleSection = (section: string) => {
  expandedSections.value[section] = !isExpanded(section)
}

watch(
  () => selectedCriteriaSource.value,
  (next) => {
    // loadCase owns its preset-rubric load. Avoid starting a second rubric
    // reload while the report list still belongs to the previous case.
    if (next && next !== store.activeCriteriaFile) {
      void store.changeCriteriaFile(next)
    }
  },
  { immediate: true },
)

watch(
  () => store.activeCriteriaFile,
  (file) => {
    selectedCriteriaSource.value = file || ''
  },
)

watch(
  () => store.activeCriteriaId,
  (criteriaId) => {
    void scrollActiveCriterionIntoView(criteriaId)
  },
)

watch(
  () => [store.activeReportId, store.activeCriteriaFile],
  () => {
    autoGradeMessage.value = ''
    autoGradeError.value = ''
  },
)

const confirmCreate = async () => {
  if (creating.value) return
  creating.value = true
  const language =
    createForm.value.language === 'custom' && createForm.value.customLanguage.trim()
      ? createForm.value.customLanguage.trim()
      : createForm.value.language

  store.startNewCriteria({
    title: createForm.value.title || 'New Criteria',
    background: createForm.value.background,
    base_score: Number.isFinite(createForm.value.base_score) ? createForm.value.base_score : 100,
    language,
  })
  try {
    const fileName = `${createForm.value.title || 'New Criteria'}.json`
    await store.saveCriteriaFile(fileName)
    selectedCriteriaSource.value = store.activeCriteriaFile || selectedCriteriaSource.value
  } catch (err) {
    console.error('[criteria] create/save failed', err)
  } finally {
    creating.value = false
    showCreateModal.value = false
  }
}

const openCreateCriteria = () => {
  showCreateModal.value = true
}

const deleteActiveCriteriaFile = async () => {
  if (!store.activeCriteriaFile || deletingCriteriaFile.value) return
  const label = criteriaFileLabel(store.activeCriteriaFile)
  if (
    !window.confirm(
      `Delete criteria "${label}"? This removes the criteria file, but not the reports.`,
    )
  )
    return
  deletingCriteriaFile.value = true
  try {
    await store.deleteCriteriaFile(store.activeCriteriaFile)
    selectedCriteriaSource.value = store.activeCriteriaFile || ''
  } catch (err) {
    console.error('[criteria] delete file failed', err)
    const message = err instanceof Error ? err.message : String(err)
    window.alert(`Failed to delete criteria: ${message}`)
  } finally {
    deletingCriteriaFile.value = false
  }
}

const openCategoryUpdate = async () => {
  if (
    !store.activeCriteriaFile ||
    !store.criteriaSchema?.criteria.length ||
    categoryPreviewLoading.value
  )
    return
  categoryPreviewLoading.value = true
  categoryError.value = ''
  try {
    categoryPreview.value = await store.previewCriteriaCategories()
  } catch (error) {
    const message =
      error instanceof Error ? error.message : $t('Failed to generate category preview')
    window.alert(message)
  } finally {
    categoryPreviewLoading.value = false
  }
}

const closeCategoryUpdate = () => {
  if (categorySaving.value) return
  categoryPreview.value = null
  categoryError.value = ''
}

const applyCategoryUpdate = async (
  assignments: Array<{ criteria_id: string; section: string }>,
) => {
  if (!categoryPreview.value || categorySaving.value) return
  categorySaving.value = true
  categoryError.value = ''
  try {
    await store.applyCriteriaCategories(categoryPreview.value.source_content_hash, assignments)
    categoryPreview.value = null
  } catch (error) {
    categoryError.value = error instanceof Error ? error.message : $t('Failed to apply categories')
  } finally {
    categorySaving.value = false
  }
}

const deleteCriteriaItem = async (criteriaId: string) => {
  if (deletingCriteriaId.value) return
  const item = store.criteriaSchema?.criteria.find(
    (entry) => String(entry.criteria_id) === String(criteriaId),
  )
  const label = item?.criteria_name || criteriaId
  if (!window.confirm(`Delete criterion "${label}" and its grades in this criteria file?`)) return
  deletingCriteriaId.value = criteriaId
  try {
    await store.deleteCriteriaItem(criteriaId)
  } catch (err) {
    console.error('[criteria] delete item failed', err)
    const message = err instanceof Error ? err.message : String(err)
    window.alert(`Failed to delete criterion: ${message}`)
  } finally {
    deletingCriteriaId.value = null
  }
}

const startAutoGrading = async () => {
  if (!canStartAutoGrade.value) return
  batchStarting.value = true
  autoGradeMessage.value = ''
  autoGradeError.value = ''
  try {
    const started = await store.startBatchAutoGrading()
    if (!started) {
      autoGradeError.value =
        'Batch auto-grading could not be started. Check the case and criteria selection.'
      return
    }
    autoGradeMessage.value =
      started.status === 'awaiting_calibration'
        ? $t('Batch prepared. Confirm the current rubric below to start all reports.')
        : `Batch auto-grading started for ${store.reports.length} reports.`
    store.toggleMode('global')
  } catch (err) {
    autoGradeError.value = err instanceof Error ? err.message : 'Auto-grading failed'
  } finally {
    batchStarting.value = false
  }
}

const cancelCreate = () => {
  showCreateModal.value = false
  selectedCriteriaSource.value = store.activeCriteriaFile || ''
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-white" @click="onCriteriaViewClick">
    <div class="border-b border-slate-100 px-5 py-4">
      <div class="flex flex-wrap items-center justify-between gap-3 text-sm">
        <div class="flex items-center gap-2">
          <span class="font-medium text-slate-500">{{ $t('Criteria') }}</span>
          <div class="relative">
            <select
              v-model="selectedCriteriaSource"
              data-testid="criteria-file-select"
              :disabled="managedExperimentCriteria"
              class="w-48 appearance-none rounded-lg border border-slate-200 bg-white py-1.5 pl-3 pr-8 text-sm font-medium text-slate-700 shadow-sm transition-all hover:border-slate-300 focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
            >
              <option
                v-for="option in criteriaSourceOptions"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>
            <div
              class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-slate-500"
            >
              <svg class="h-4 w-4 fill-current" viewBox="0 0 20 20">
                <path
                  d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                />
              </svg>
            </div>
          </div>
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-600 shadow-sm transition-colors hover:border-[#86b5b0] hover:bg-[var(--interaction-active-soft)] hover:text-[var(--interaction-active)] focus:outline-none focus:ring-2 focus:ring-[#b7d2ce] disabled:cursor-not-allowed disabled:opacity-40"
            :title="$t('Update criteria categories')"
            :disabled="
              !store.activeCriteriaFile ||
              !store.criteriaSchema?.criteria.length ||
              categoryPreviewLoading
            "
            data-testid="update-criteria-categories"
            @click="openCategoryUpdate"
          >
            <svg
              class="h-3.5 w-3.5"
              :class="{ 'animate-spin': categoryPreviewLoading }"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h10M4 18h7" />
            </svg>
            {{ categoryPreviewLoading ? $t('Organizing categories…') : $t('Update categories') }}
          </button>
          <button
            v-if="!managedExperimentCriteria"
            type="button"
            class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition-colors hover:border-[#86b5b0] hover:bg-[var(--interaction-active-soft)] hover:text-[var(--interaction-active)] focus:outline-none focus:ring-2 focus:ring-[#b7d2ce]"
            :title="$t('Create criteria')"
            :aria-label="$t('Create criteria')"
            @click="openCreateCriteria"
          >
            <svg
              class="h-4 w-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <button
            v-if="!managedExperimentCriteria"
            type="button"
            class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition-colors hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 focus:outline-none focus:ring-2 focus:ring-rose-200 disabled:cursor-not-allowed disabled:opacity-40"
            :title="$t('Delete selected criteria')"
            :aria-label="$t('Delete selected criteria')"
            :disabled="!store.activeCriteriaFile || deletingCriteriaFile"
            @click="deleteActiveCriteriaFile"
          >
            <svg
              v-if="!deletingCriteriaFile"
              class="h-4 w-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M3 6h18" />
              <path stroke-linecap="round" stroke-linejoin="round" d="M8 6V4h8v2" />
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14H6L5 6" />
              <path stroke-linecap="round" stroke-linejoin="round" d="M10 11v5M14 11v5" />
            </svg>
            <svg v-else class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
              />
            </svg>
          </button>
        </div>

        <div class="flex items-center gap-2">
          <button
            v-if="missedAutoGradeCriteria.length"
            type="button"
            data-testid="jump-next-ungraded"
            data-keep-card-selection
            class="inline-flex items-center gap-1 whitespace-nowrap rounded-lg border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs font-semibold text-amber-800 shadow-sm transition-colors hover:border-amber-400 hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-200"
            :title="
              $t('Go to the next criterion without a saved grade ({count} remaining)', {
                count: missedAutoGradeCriteria.length,
              })
            "
            @click="jumpToNextUngraded"
          >
            <svg
              class="h-3.5 w-3.5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              aria-hidden="true"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14m-5-5 5 5-5 5" />
            </svg>
            {{ $t('Next ungraded') }} · {{ missedAutoGradeCriteria.length }}
          </button>
          <button
            type="button"
            data-testid="start-auto-grading"
            class="inline-flex items-center gap-1.5 rounded-lg bg-[var(--interaction-active)] px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-[var(--interaction-active-strong)] focus:outline-none focus:ring-2 focus:ring-[#86b5b0] disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500"
            :disabled="!canStartAutoGrade"
            :title="autoGradeButtonTitle"
            @click="startAutoGrading"
          >
            <svg
              v-if="autoGradeRunning"
              class="h-3.5 w-3.5 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
              />
            </svg>
            <svg
              v-else
              class="h-3.5 w-3.5"
              viewBox="0 0 24 24"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                d="M8 5.14v13.72a1 1 0 001.51.86l10.31-6.86a1 1 0 000-1.72L9.51 4.28A1 1 0 008 5.14z"
              />
            </svg>
            {{
              store.autoGradeBatchStarted
                ? $t('Auto-grading active')
                : autoGradeRunning
                  ? $t('Starting...')
                  : $t('Start auto-grading')
            }}
          </button>
        </div>
      </div>
      <div
        v-if="autoGradeMessage || autoGradeError"
        data-testid="auto-grade-feedback"
        class="mt-3 rounded-lg border px-3 py-2 text-xs"
        :class="
          autoGradeError
            ? 'border-rose-200 bg-rose-50 text-rose-700'
            : 'border-emerald-200 bg-emerald-50 text-emerald-700'
        "
        role="status"
      >
        {{ autoGradeError || autoGradeMessage }}
      </div>
      <ReportScoreProfile
        v-if="
          store.criteriaSchema &&
          (store.criteriaSchema.criteria.length ||
            visibleImplicitGrades.length ||
            store.currentReportPendingProposals.length)
        "
        class="mt-3"
        :criteria="store.criteriaSchema.criteria"
        :gradings="Array.from(gradingMap.values())"
        :proposals="store.currentReportPendingProposals"
        :pending-items="visibleImplicitGrades"
        :active-pending-id="store.activeImplicitGradeId"
        :highlights="store.reportHighlights"
        :report-score="store.currentReportScore"
        :base-score="Number(store.criteriaSchema.base_score) || 100"
        :active-criteria-id="store.activeCriteriaId"
        :conflict-criteria-ids="Array.from(conflictsByCriteria.keys())"
        :review-criteria-ids="reviewCriteriaIds"
        :proposal-saving-id="profileProposalSavingId"
        :pending-retrying-id="retryingImplicitGradeId"
        :proposal-error="profileProposalError"
        @select-criterion="onSelectCriteria"
        @review-proposal="openProposal"
        @confirm-proposal="confirmProfileProposal"
        @select-pending="selectImplicitGrade"
        @edit-pending="openImplicitEditor"
        @retry-pending="retryImplicitGrade"
      />
    </div>

    <div
      data-testid="criteria-list"
      class="flex-1 min-h-0 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-300 scrollbar-track-transparent"
    >
      <div class="min-h-full px-5 py-4">
        <div
          v-if="!store.criteriaSchema && !visibleImplicitGrades.length"
          class="flex min-h-full flex-col items-center justify-center text-center"
        >
          <div class="rounded-full bg-slate-50 p-3 mb-3">
            <svg
              class="w-6 h-6 text-slate-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
              />
            </svg>
          </div>
          <p class="text-sm text-slate-500">
            {{ $t('Please select or create criteria to begin.') }}
          </p>
        </div>
        <div class="space-y-6">
          <div
            v-if="store.alignmentError"
            class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800"
          >
            Alignment state unavailable: {{ store.alignmentError }}
          </div>
          <details
            v-if="store.openConflicts.length"
            data-keep-card-selection
            class="rounded-lg border border-rose-200 bg-rose-50/50"
          >
            <summary
              class="cursor-pointer list-none px-3 py-2 text-[10px] font-semibold text-rose-800"
            >
              {{ $t('Cross-report conflict tasks') }} ·
              {{ store.openConflicts.length }}
              <span class="ml-1 font-normal text-rose-600">
                {{ $t('Current-report conflicts are handled inside their table rows.') }}
              </span>
            </summary>
            <div class="border-t border-rose-200 p-3">
              <ConflictReviewPanel
                :conflicts="store.openConflicts"
                @select-criterion="onSelectCriteria"
              />
            </div>
          </details>

          <div
            v-if="
              store.criteriaSchema &&
              !store.criteriaSchema.criteria.length &&
              !visibleImplicitGrades.length
            "
            class="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-12 text-center"
          >
            <div class="mb-3 rounded-full bg-white p-3 text-slate-400 shadow-sm">
              <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M9 12h6m-6 4h6M7 3h7l5 5v13H7a2 2 0 01-2-2V5a2 2 0 012-2z"
                />
              </svg>
            </div>
            <div class="text-sm font-semibold text-slate-700">
              {{ $t('No criteria in this file') }}
            </div>
            <p class="mt-1 max-w-[260px] text-xs leading-relaxed text-slate-500">
              Confirmed standards for this criteria file will appear here.
            </p>
          </div>

          <div
            v-if="store.criteriaSchema?.criteria.length"
            class="overflow-hidden rounded-lg border border-slate-200"
            data-testid="assessment-table"
          >
            <div
              class="sticky top-0 z-10 grid grid-cols-[minmax(10rem,1.05fr)_minmax(12rem,1.25fr)_minmax(10rem,.8fr)] bg-slate-100/95 text-[10px] font-bold text-slate-600 backdrop-blur"
              data-testid="assessment-table-header"
            >
              <div class="px-4 py-2">{{ $t('Scoring requirement') }}</div>
              <div class="border-l border-slate-200 px-4 py-2">
                {{ $t('Current report evidence') }}
              </div>
              <div class="border-l border-slate-200 px-4 py-2">
                {{ $t('Score and status') }}
              </div>
            </div>

            <div v-for="[section, items] in groupedCriteria" :key="section" class="space-y-0">
              <button
                data-testid="criteria-section-toggle"
                class="group flex w-full items-center justify-between border-t border-slate-200 bg-slate-50 px-3 py-2 text-left transition-colors hover:bg-slate-100"
                @click="toggleSection(section)"
              >
                <span
                  class="text-sm font-bold tracking-tight text-slate-800 transition-colors group-hover:text-[var(--interaction-active)]"
                  >{{ section }}</span
                >
                <span
                  class="ml-2 flex h-5 w-5 items-center justify-center rounded bg-slate-100 text-slate-400 transition-transform duration-200"
                  :class="{ 'rotate-180': isExpanded(section) }"
                >
                  <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </span>
              </button>
              <div
                v-show="isExpanded(section)"
                class="overflow-hidden"
                data-testid="assessment-table-section"
              >
                <div
                  v-for="item in items"
                  :key="item.criteria_id"
                  :ref="(element) => setCriterionCardElement(item.criteria_id, element)"
                  :data-criterion-id="item.criteria_id"
                  data-testid="criterion-card"
                  data-selectable-card
                  class="w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--interaction-active)]"
                  role="button"
                  tabindex="0"
                  @click="onSelectCriteria(item.criteria_id)"
                  @keydown.enter.prevent="onSelectCriteria(item.criteria_id)"
                  @keydown.space.prevent="onSelectCriteria(item.criteria_id)"
                >
                  <ReportAssessmentRow
                    :item="item"
                    :grading="getGrading(item.criteria_id)"
                    :active="store.activeCriteriaId === String(item.criteria_id)"
                    :report-name="store.activeReportId"
                    :readiness="getReadiness(item.criteria_id)"
                    :conflicts="getConflicts(item.criteria_id)"
                    :abstention="getAbstention(item.criteria_id)"
                    :auto-grade-job="getAutoGradeJob(item.criteria_id)"
                    :audit-job="getReviewJob(item.criteria_id)"
                    :pending-proposals="getPendingProposals(item.criteria_id)"
                    :authorization="getAuthorization(item.criteria_id)"
                    :consistency="getConsistency(item.criteria_id)"
                    :review-assignment="getReviewAssignment(item.criteria_id)"
                    :authorization-busy="authorizationActionId === String(item.criteria_id)"
                    :can-accept-model-grade="
                      store.canAcceptModelGrade(getGrading(item.criteria_id))
                    "
                    :model-acceptance-busy="modelAcceptanceActionId === String(item.criteria_id)"
                    :grade-saving="inlineGradeSavingId === String(item.criteria_id)"
                    :rubric-saving="inlineRubricSavingId === String(item.criteria_id)"
                    :action-error="inlineActionErrors[String(item.criteria_id)] || ''"
                    :evidence-segments="getEvidenceSegments(item.criteria_id)"
                    :active-evidence-segment-id="
                      store.activeCriteriaId === String(item.criteria_id)
                        ? store.activeHighlightId
                        : null
                    "
                    @delete="deleteCriteriaItem"
                    @select="onSelectCriteria"
                    @toggle="onToggleCriteria"
                    @authorize="authorizeCriterion"
                    @confirm-repair="confirmCriterionRepair"
                    @edit="openCriterionEditor"
                    @save-grade="saveInlineGrade"
                    @save-rubric="saveInlineRubric"
                    @accept-model="acceptModelPreGrade"
                    @review-proposal="openProposal"
                    @evidence-select="onSelectEvidenceSegment"
                    @evidence-adjust="onAdjustEvidence"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <CriteriaEditorModal
    v-if="editorOpen"
    :initial-tab="editorInitialTab"
    @close="editorOpen = false"
  />

  <CriteriaCategoryUpdateModal
    v-if="categoryPreview"
    :preview="categoryPreview"
    :saving="categorySaving"
    :error="categoryError"
    @close="closeCategoryUpdate"
    @confirm="applyCategoryUpdate"
  />

  <div
    v-if="showCreateModal"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm transition-opacity"
  >
    <div
      class="w-full max-w-[480px] scale-100 transform rounded-2xl bg-white p-6 shadow-2xl transition-all"
    >
      <div class="mb-5 flex items-center justify-between">
        <h3 class="text-lg font-bold text-slate-800">
          {{ $t('Create New Criteria') }}
        </h3>
        <button class="text-slate-400 hover:text-slate-600" @click="cancelCreate">
          <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
            <path
              fill-rule="evenodd"
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
              clip-rule="evenodd"
            />
          </svg>
        </button>
      </div>
      <div class="space-y-4">
        <div class="space-y-1.5">
          <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
            $t('Title')
          }}</label>
          <input
            v-model="createForm.title"
            class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 placeholder-slate-400 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
            :placeholder="$t('e.g. Physics Lab Grading')"
          />
        </div>
        <div class="space-y-1.5">
          <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
            $t('Background Context')
          }}</label>
          <textarea
            v-model="createForm.background"
            rows="3"
            class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 placeholder-slate-400 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
            :placeholder="$t('Optional context for the grading criteria...')"
          ></textarea>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div class="space-y-1.5">
            <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
              $t('Full Score')
            }}</label>
            <input
              v-model.number="createForm.base_score"
              type="number"
              min="0"
              class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
            />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
              $t('Language')
            }}</label>
            <div class="relative">
              <select
                v-model="createForm.language"
                class="w-full appearance-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
              >
                <option v-for="lang in languageOptions" :key="lang" :value="lang">
                  {{ lang }}
                </option>
                <option value="custom">{{ $t('Custom...') }}</option>
              </select>
              <div
                class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-slate-500"
              >
                <svg class="h-4 w-4 fill-current" viewBox="0 0 20 20">
                  <path
                    d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                  />
                </svg>
              </div>
            </div>
          </div>
        </div>
        <div v-if="createForm.language === 'custom'" class="space-y-1.5 animate-fade-in">
          <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
            $t('Language Code')
          }}</label>
          <input
            v-model="createForm.customLanguage"
            class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
            placeholder="e.g. zh-CN"
          />
        </div>
      </div>
      <div class="mt-8 flex justify-end gap-3">
        <button
          class="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-800"
          @click="cancelCreate"
        >
          Cancel
        </button>
        <button
          class="rounded-lg bg-[var(--interaction-active)] px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-[var(--interaction-active-strong)] focus:outline-none focus:ring-2 focus:ring-[var(--interaction-active)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="creating"
          @click="confirmCreate"
        >
          {{ creating ? $t('Creating...') : $t('Create Criteria') }}
        </button>
      </div>
    </div>
  </div>

  <div
    v-if="proposalModalOpen && activeProposal && proposalDraft"
    data-testid="proposal-review-modal"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-4 backdrop-blur-sm"
  >
    <div class="flex max-h-[88vh] w-full max-w-[960px] flex-col rounded-2xl bg-white shadow-2xl">
      <div class="flex items-start justify-between border-b border-slate-100 px-6 py-5">
        <div>
          <div class="text-xs font-semibold uppercase tracking-wide text-[var(--status-review)]">
            {{
              activeProposal.proposal_type === 'create_criterion'
                ? $t('Create criterion proposal')
                : $t('Update criterion proposal')
            }}
          </div>
          <h3 class="mt-1 text-lg font-bold text-slate-800">
            {{ $t('Review induced scoring standard') }}
          </h3>
          <p class="mt-1 text-xs text-slate-500">
            {{
              $t('{count} source grading act(s) · version', {
                count: proposalSourceCount(activeProposal),
              })
            }}
            {{ activeProposal.proposal_version }}
          </p>
        </div>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="rounded-lg border border-[#d8b77f] bg-[#f8efe1] px-3 py-1.5 text-xs font-semibold text-[#79531d] hover:bg-[#f2dfbf]"
            @click="proposalEditing = !proposalEditing"
          >
            {{ proposalEditing ? $t('Finish editing') : $t('Edit') }}
          </button>
          <button class="text-slate-400 hover:text-slate-600" @click="closeProposal">
            <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fill-rule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clip-rule="evenodd"
              />
            </svg>
          </button>
        </div>
      </div>
      <div
        class="min-h-0 flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-300 scrollbar-track-transparent"
      >
        <div class="space-y-4 px-6 py-5">
          <section class="border-b border-slate-100 pb-4">
            <div class="text-xs font-semibold uppercase tracking-wide text-[var(--status-review)]">
              {{ $t('Reason for update') }}
            </div>
            <p class="mt-1 text-sm leading-relaxed text-[#5e421b]">
              {{
                activeProposal.rationale ||
                $t('The model recommends updating this rubric based on teacher grading records.')
              }}
            </p>
          </section>

          <section
            v-if="proposalSourceReasons.length"
            class="space-y-2 border-b border-slate-100 pb-4"
          >
            <div class="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Source annotation rationale
            </div>
            <div
              v-for="item in proposalSourceReasons"
              :key="item.assignment.implicit_grade_id"
              class="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm"
            >
              <div class="font-medium text-slate-700">
                {{ item.reportName
                }}<span v-if="item.score !== undefined" class="ml-1 text-slate-500"
                  >· {{ item.score }} pts</span
                >
              </div>
              <p class="mt-1 leading-relaxed text-slate-600">
                {{
                  item.teacherReason ||
                  item.assignment.generated_score_reason ||
                  'The model could not generate a rationale. Add supporting evidence before confirmation.'
                }}
              </p>
              <div
                class="mt-1 text-[11px] font-medium"
                :class="item.teacherReason ? 'text-slate-500' : 'text-[var(--status-review)]'"
              >
                {{
                  item.teacherReason
                    ? $t('Teacher-provided')
                    : $t('Model draft · awaiting teacher review')
                }}
              </div>
            </div>
          </section>

          <section class="space-y-1.5 border-b border-slate-100 pb-4">
            <label class="block text-xs font-semibold uppercase tracking-wide text-slate-500">{{
              $t('Criterion after review')
            }}</label>
            <select
              v-model="proposalTargetCriterionId"
              class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-[var(--status-review)] focus:outline-none focus:ring-1 focus:ring-[var(--status-review)]"
            >
              <option :value="null">{{ $t('Create new criterion') }}</option>
              <option
                v-for="criterion in store.criteriaSchema?.criteria || []"
                :key="criterion.criteria_id"
                :value="String(criterion.criteria_id)"
              >
                {{ $t('Merge into') }}: {{ criterion.criteria_name }}
              </option>
            </select>
            <p class="text-xs leading-relaxed text-slate-500">
              Correct the model-matched criterion before confirmation. When creating a new
              criterion, the maximum score below defines its total score.
            </p>
          </section>

          <section v-if="!proposalEditing" class="space-y-2">
            <div class="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {{ $t('Scoring point changes') }}
            </div>
            <ScoringPointsDiff
              :before="reviewedProposalTarget?.scoring_points || []"
              :after="proposalDraft.scoring_points"
              :empty-text="
                reviewedProposalTarget
                  ? $t('No scoring point changes.')
                  : $t('The following scoring points will be added.')
              "
            />
          </section>

          <section v-else class="space-y-4">
            <div class="grid gap-3 md:grid-cols-3">
              <label class="space-y-1"
                ><span class="text-[11px] font-semibold text-slate-500">{{
                  $t('Criterion name')
                }}</span
                ><input
                  v-model="proposalDraft.criteria_name"
                  class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-[var(--status-review)] focus:outline-none focus:ring-1 focus:ring-[var(--status-review)]"
              /></label>
              <label class="space-y-1"
                ><span class="text-[11px] font-semibold text-slate-500">{{
                  $t('Criteria category')
                }}</span
                ><input
                  v-model="proposalDraft.section"
                  class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-[var(--status-review)] focus:outline-none focus:ring-1 focus:ring-[var(--status-review)]"
              /></label>
              <label class="space-y-1"
                ><span class="text-[11px] font-semibold text-slate-500">{{
                  $t('Maximum score')
                }}</span
                ><input
                  v-model.number="proposalDraft.max_score"
                  type="number"
                  min="0"
                  class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-[var(--status-review)] focus:outline-none focus:ring-1 focus:ring-[var(--status-review)]"
              /></label>
            </div>
            <StructuredRubricEditor
              v-model="proposalDraft.scoring_points"
              :criterion-id="proposalDraft.criteria_id"
              :max-score="proposalDraft.max_score"
            />
          </section>

          <section class="space-y-2 border-t border-slate-100 pt-4">
            <div class="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Overall description changes
            </div>
            <div v-if="!proposalEditing" class="grid gap-3 md:grid-cols-2 text-xs leading-relaxed">
              <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div class="mb-1 font-semibold text-slate-500">
                  {{ $t('Before') }}
                </div>
                <p class="whitespace-pre-wrap text-slate-700">
                  {{ reviewedProposalTarget?.description || '(New criterion)' }}
                </p>
              </div>
              <div class="rounded-lg border border-[#d8b77f] bg-[#f8efe1] p-3">
                <div class="mb-1 font-semibold text-[#79531d]">
                  {{ $t('After') }}
                </div>
                <p class="whitespace-pre-wrap text-[#5e421b]">
                  {{ proposalDraft.description }}
                </p>
              </div>
            </div>
            <textarea
              v-else
              v-model="proposalDraft.description"
              rows="5"
              class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-[var(--status-review)] focus:outline-none focus:ring-1 focus:ring-[var(--status-review)]"
            />
          </section>
          <div
            v-if="proposalError"
            class="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          >
            {{ proposalError }}
          </div>
        </div>
      </div>
      <div class="flex justify-end gap-3 border-t border-slate-100 px-6 py-4">
        <button
          class="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          :disabled="proposalSaving"
          @click="rejectProposal"
        >
          Reject
        </button>
        <button
          class="rounded-lg bg-[var(--status-review)] px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[#94631f] disabled:opacity-60"
          :disabled="proposalSaving"
          @click="confirmProposal"
        >
          {{ proposalSaving ? $t('Saving...') : $t('Confirm standard') }}
        </button>
      </div>
    </div>
  </div>
</template>
