<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type {
  AlignmentConflict,
  AlignmentReadiness,
  CriteriaItem,
  CriterionAuthorization,
  CriterionConsistencyState,
  GradingItem,
  HighlightItem,
  ScoringPoint,
  ScoringPointScore,
} from '../../types/schema'
import { translate as t } from '../../i18n'
import {
  cloneScoringPoints,
  newUniqueScoringPoint,
  normalizeScoringPointScores,
  scoringPointDeductionCapacity,
  structuredDeductionCapacity,
  structuredPointScoreTotal,
  updateScoringPointContent,
  updateScoringPointScore,
} from '../../utils/scoringPoints'
import {
  evidenceSegmentLabel,
  formatAssessmentScore,
  graderType,
} from '../../utils/reportAssessmentPresentation'
import {
  authorizationState,
  consistencyLabel,
  consistencyPercent,
} from '../criteriaCardPresentation'
import ConflictReviewPanel from '../ConflictReviewPanel.vue'
import type {
  ReportAssessmentMode,
  ReportGradeDraft,
  ReportRubricDraft,
} from './reportAssessmentTypes'

const props = withDefaults(
  defineProps<{
    item: CriteriaItem
    grading?: GradingItem
    mode: ReportAssessmentMode
    reportName?: string
    readiness?: AlignmentReadiness | null
    conflicts?: AlignmentConflict[]
    authorization?: CriterionAuthorization | null
    consistency?: CriterionConsistencyState | null
    authorizationBusy?: boolean
    canAcceptModelGrade?: boolean
    modelAcceptanceBusy?: boolean
    gradeSaving?: boolean
    rubricSaving?: boolean
    actionError?: string
    evidenceSegments?: HighlightItem[]
    activeEvidenceSegmentId?: string | null
  }>(),
  {
    reportName: '',
    conflicts: () => [],
    evidenceSegments: () => [],
    actionError: '',
  },
)

const emit = defineEmits<{
  (event: 'update:mode', mode: ReportAssessmentMode): void
  (event: 'delete', criteriaId: string): void
  (event: 'authorize', criteriaId: string): void
  (event: 'confirm-repair', criteriaId: string): void
  (event: 'accept-model', criteriaId: string): void
  (event: 'advanced-edit', criteriaId: string): void
  (event: 'save-grade', draft: ReportGradeDraft): void
  (event: 'save-rubric', draft: ReportRubricDraft): void
  (event: 'evidence-adjust', payload: { criteriaId: string; highlightId: string | null }): void
}>()

const scoreDraft = ref(0)
const reasonDraft = ref('')
const pointScores = ref<ScoringPointScore[]>([])
const matchedRuleIds = ref<string[]>([])
const teacherDraftTouched = ref(false)
const expandedPointId = ref<string | null>(null)
const reasonTextarea = ref<HTMLTextAreaElement | null>(null)
const rubricDraft = ref<CriteriaItem>(JSON.parse(JSON.stringify(props.item)) as CriteriaItem)

const cleanScoringRationale = (reason: string) =>
  reason.replace(/\s*Earned\s+\d+(?:\.\d+)?\s+out\s+of\s+\d+(?:\.\d+)?[。.]?\s*$/i, '').trim()

const baselineReason = computed(() => cleanScoringRationale(props.grading?.score_reason || ''))

const gradeFingerprint = computed(() =>
  JSON.stringify({
    score: props.grading?.criteria_score ?? null,
    reason: props.grading?.score_reason || '',
    points: props.grading?.scoring_point_scores || [],
    matchedPoints: props.grading?.matched_scoring_points || [],
    matchedRules: props.grading?.matched_deduction_rules || [],
    criterionPoints: props.item.scoring_points,
  }),
)

watch(
  gradeFingerprint,
  () => {
    scoreDraft.value = Number(props.grading?.criteria_score ?? 0)
    reasonDraft.value = baselineReason.value
    teacherDraftTouched.value = false
    expandedPointId.value = null
    matchedRuleIds.value = [...(props.grading?.matched_deduction_rules || [])]
    pointScores.value = normalizeScoringPointScores(
      props.item.scoring_points,
      props.grading?.scoring_point_scores,
      props.grading?.criteria_score ?? null,
    )
  },
  { immediate: true },
)

watch(
  () => props.item,
  (item) => {
    rubricDraft.value = {
      ...JSON.parse(JSON.stringify(item)),
      scoring_points: cloneScoringPoints(item.scoring_points),
    } as CriteriaItem
  },
  { immediate: true, deep: true },
)

const resizeReasonTextarea = () => {
  const textarea = reasonTextarea.value
  if (!textarea) return
  textarea.style.height = 'auto'
  const borderHeight = textarea.offsetHeight - textarea.clientHeight
  if (textarea.scrollHeight) textarea.style.height = `${textarea.scrollHeight + borderHeight}px`
}

watch(
  [reasonDraft, () => props.mode],
  () => {
    void nextTick(resizeReasonTextarea)
  },
  { immediate: true },
)

const firstEvidence = computed(() => props.evidenceSegments[0] || null)
const activeEvidence = computed(
  () =>
    props.evidenceSegments.find((segment) => segment.id === props.activeEvidenceSegmentId) ||
    firstEvidence.value,
)
const evidenceText = computed(
  () =>
    (activeEvidence.value ? evidenceSegmentLabel(activeEvidence.value) : '') ||
    props.grading?.anchor_text ||
    '',
)
const hasStructuredScoring = computed(() => props.item.scoring_points.length > 0)
const normalizedPointScores = computed(() =>
  normalizeScoringPointScores(props.item.scoring_points, pointScores.value),
)
const pointScoreById = computed(
  () => new Map(normalizedPointScores.value.map((score) => [score.point_id, score.score])),
)
const pointReasonById = computed(
  () => new Map(normalizedPointScores.value.map((score) => [score.point_id, score.reason || ''])),
)
const originalPointScoreById = computed(
  () =>
    new Map(
      normalizeScoringPointScores(
        props.item.scoring_points,
        props.grading?.scoring_point_scores,
        props.grading?.criteria_score ?? null,
      ).map((score) => [score.point_id, score.score]),
    ),
)
const currentScore = computed(() => Number(props.grading?.criteria_score ?? 0))
const totalScore = computed(() =>
  hasStructuredScoring.value
    ? structuredPointScoreTotal(normalizedPointScores.value)
    : Math.min(Math.max(Number(scoreDraft.value) || 0, 0), Number(props.item.max_score) || 0),
)
const hasTeacherDraft = computed(() => {
  if (!props.grading) return teacherDraftTouched.value
  return (
    Math.abs(totalScore.value - currentScore.value) > 1e-6 ||
    reasonDraft.value !== baselineReason.value ||
    JSON.stringify([...matchedRuleIds.value].sort()) !==
      JSON.stringify([...(props.grading.matched_deduction_rules || [])].sort())
  )
})
const sourceType = computed(() => graderType(props.grading))
const sourceLabel = computed(() => {
  if (sourceType.value === 'model' || sourceType.value === 'llm') {
    return props.grading?.acceptance_status === 'accepted_model'
      ? t('Model grade · accepted')
      : t('Model pre-grade')
  }
  if (sourceType.value === 'teacher' || sourceType.value === 'user') return t('Teacher grade')
  return t('No grade')
})

const authorization = computed(() => authorizationState(props.authorization))
const canAuthorize = computed(
  () =>
    authorization.value === 'teacher_validation' &&
    props.readiness?.lifecycle_state === 'active_selective',
)
const consistencyReliable = computed(() => props.consistency?.state === 'reliable')

const POINT_SCORE_STEP = 1
const pointMaximum = (point: ScoringPoint) => scoringPointDeductionCapacity(point)
const pointScore = (point: ScoringPoint) => pointScoreById.value.get(point.point_id) || 0
const pointReason = (point: ScoringPoint) => pointReasonById.value.get(point.point_id)?.trim() || ''
const originalPointScore = (point: ScoringPoint) =>
  originalPointScoreById.value.get(point.point_id) || 0
const pointHasDraftChange = (point: ScoringPoint) =>
  Math.abs(pointScore(point) - originalPointScore(point)) > 1e-6
const pointRuleIds = (point: ScoringPoint) =>
  new Set((point.deduction_rules || []).map((rule) => String(rule.rule_id)))
const pointScoreSegments = (point: ScoringPoint) => {
  const maximum = pointMaximum(point)
  return Array.from({ length: Math.max(1, Math.ceil(maximum / POINT_SCORE_STEP)) }, (_, index) =>
    Math.min((index + 1) * POINT_SCORE_STEP, maximum),
  )
}
const togglePointDetails = (pointId: string) => {
  expandedPointId.value = expandedPointId.value === pointId ? null : pointId
}
const setPointScore = (point: ScoringPoint, score: number, clearPointRules = true) => {
  const maximum = pointMaximum(point)
  const boundedScore = Math.min(Math.max(score, 0), maximum)
  teacherDraftTouched.value = true
  if (clearPointRules) {
    const ruleIds = pointRuleIds(point)
    matchedRuleIds.value = matchedRuleIds.value.filter((ruleId) => !ruleIds.has(String(ruleId)))
  }
  pointScores.value = normalizedPointScores.value.map((item) =>
    item.point_id === point.point_id
      ? { ...item, score: boundedScore, max_score: maximum, reason: null }
      : item,
  )
}
const updatePointScore = (point: ScoringPoint, raw: string) => {
  const parsed = Number(raw)
  setPointScore(point, Number.isFinite(parsed) ? parsed : 0)
}
const selectPointScoreSegment = (point: ScoringPoint, segmentScore: number) => {
  const nextScore =
    pointScore(point) === segmentScore && segmentScore <= POINT_SCORE_STEP ? 0 : segmentScore
  setPointScore(point, nextScore)
}

const adjustEvidence = () => {
  emit('evidence-adjust', {
    criteriaId: String(props.item.criteria_id),
    highlightId: activeEvidence.value?.id || null,
  })
}

const saveGrade = () => {
  emit('save-grade', {
    criteriaId: String(props.item.criteria_id),
    score: totalScore.value,
    reason: reasonDraft.value,
    anchorText: evidenceText.value,
    matchedScoringPoints: hasStructuredScoring.value
      ? props.item.scoring_points
          .filter((point) => pointScore(point) < pointMaximum(point))
          .map((point) => point.point_id)
      : [],
    matchedDeductionRules: [...matchedRuleIds.value],
    scoringPointScores: hasStructuredScoring.value ? normalizedPointScores.value : [],
  })
}

const rubricBudget = computed(() =>
  structuredDeductionCapacity(
    rubricDraft.value.scoring_points,
    rubricDraft.value.scoring_aggregation,
  ),
)
const rubricBudgetValid = computed(
  () => Math.abs(rubricBudget.value - Number(rubricDraft.value.max_score || 0)) < 1e-6,
)
const updatePointSummary = (pointIndex: number, summary: string) => {
  const point = rubricDraft.value.scoring_points[pointIndex]
  if (point) updateScoringPointContent(point, summary)
}
const updatePointAllocation = (pointIndex: number, raw: string) => {
  const point = rubricDraft.value.scoring_points[pointIndex]
  if (!point) return
  updateScoringPointScore(point, raw)
}
const addScoringPoint = () =>
  rubricDraft.value.scoring_points.push(
    newUniqueScoringPoint(rubricDraft.value.scoring_points, String(rubricDraft.value.criteria_id)),
  )
const removeScoringPoint = (pointIndex: number) => {
  rubricDraft.value.scoring_points.splice(pointIndex, 1)
}
const rubricAllocationShare = (point: ScoringPoint) => {
  const denominator = Math.max(rubricBudget.value, Number(rubricDraft.value.max_score) || 0, 1)
  return `${(scoringPointDeductionCapacity(point) / denominator) * 100}%`
}
const rubricCategoryValid = computed(() => Boolean(rubricDraft.value.section?.trim()))
const saveRubric = () => {
  if (!rubricBudgetValid.value || !rubricCategoryValid.value) return
  emit('save-rubric', {
    criterion: JSON.parse(JSON.stringify(rubricDraft.value)) as CriteriaItem,
  })
}
</script>

<template>
  <section
    class="border-y border-b-2 border-[#b7d2ce] bg-white"
    :aria-label="t('Expanded criterion analysis')"
    :data-testid="`assessment-detail-${props.item.criteria_id}`"
  >
    <div class="flex items-center gap-1 border-b border-slate-200 bg-slate-50/70 px-3 py-2">
      <button
        type="button"
        class="rounded-md px-2.5 py-1 text-[10px] font-semibold"
        :class="
          props.mode === 'score'
            ? 'bg-[var(--interaction-active)] text-white'
            : 'text-slate-600 hover:bg-white'
        "
        @click.stop="emit('update:mode', 'score')"
      >
        {{ t('Score and evidence') }}
      </button>
      <button
        v-if="props.conflicts.length"
        type="button"
        class="rounded-md px-2.5 py-1 text-[10px] font-semibold"
        :class="
          props.mode === 'conflict' ? 'bg-rose-600 text-white' : 'text-rose-700 hover:bg-white'
        "
        :data-testid="`assessment-mode-conflict-${props.item.criteria_id}`"
        @click.stop="emit('update:mode', 'conflict')"
      >
        {{ t('Conflict') }} · {{ props.conflicts.length }}
      </button>
      <button
        type="button"
        class="rounded-md px-2.5 py-1 text-[10px] font-semibold"
        :class="
          props.mode === 'rubric' ? 'bg-amber-500 text-white' : 'text-slate-600 hover:bg-white'
        "
        :data-testid="`assessment-mode-rubric-${props.item.criteria_id}`"
        @click.stop="emit('update:mode', 'rubric')"
      >
        {{ t('Edit scoring standard') }}
      </button>
      <span class="ml-auto text-[9px] text-slate-400">{{
        t('Only the selected row is expanded')
      }}</span>
    </div>

    <div v-if="props.mode === 'score'" class="min-w-0 px-4 py-3">
      <div
        class="grid grid-cols-[minmax(15rem,2fr)_minmax(0,3fr)] items-stretch gap-4"
        data-testid="score-rationale-layout"
      >
        <div class="min-w-0">
          <div class="mb-2.5 min-h-12" data-testid="score-column-header">
            <div class="flex h-8 items-center gap-2">
              <span class="text-[10px] font-bold text-slate-700">{{
                t('Scoring points and scores')
              }}</span>
              <span
                v-if="hasStructuredScoring"
                class="rounded-full bg-slate-100 px-2 py-0.5 text-[8px] font-semibold text-slate-500"
              >
                {{ props.item.scoring_points.length }} {{ t('scoring points') }}
              </span>
            </div>
            <div class="flex h-4 items-center text-[9px] text-slate-400">
              {{ sourceLabel }}
            </div>
          </div>

          <div v-if="hasStructuredScoring" class="space-y-2">
            <div
              v-for="point in props.item.scoring_points"
              :key="point.point_id"
              class="rounded-md border border-slate-200 bg-slate-50/30 px-3 py-1.5"
              :data-testid="`scoring-ledger-point-${point.point_id}`"
            >
              <div class="grid grid-cols-[minmax(0,1fr)_6rem] items-center gap-x-3 gap-y-1">
                <div
                  class="truncate text-[10px] font-semibold text-slate-700"
                  :title="point.summary"
                >
                  {{ point.summary }}
                </div>
                <label class="flex items-center justify-end gap-1 text-[9px] text-slate-400">
                  <input
                    type="number"
                    min="0"
                    :max="pointMaximum(point)"
                    step="1"
                    class="score-value-input h-6 w-8 rounded border border-slate-200 px-0.5 text-center text-[10px] font-semibold tabular-nums text-[var(--interaction-active)] focus:border-[var(--interaction-active)] focus:outline-none"
                    :aria-label="`${t('Set score')} / ${formatAssessmentScore(pointMaximum(point))}`"
                    :title="`${formatAssessmentScore(pointScore(point))} / ${formatAssessmentScore(pointMaximum(point))}`"
                    :data-testid="`inline-point-score-${point.point_id}`"
                    :value="pointScore(point)"
                    @input="updatePointScore(point, ($event.target as HTMLInputElement).value)"
                  />
                  <span>/ {{ formatAssessmentScore(pointMaximum(point)) }}</span>
                </label>
                <button
                  type="button"
                  class="flex min-w-0 items-center gap-1 overflow-hidden text-left text-[8px] leading-4 text-slate-500"
                  :aria-expanded="expandedPointId === point.point_id"
                  :aria-label="
                    t(
                      expandedPointId === point.point_id
                        ? 'Hide scoring-point details'
                        : 'Show scoring-point details',
                    )
                  "
                  :data-testid="`toggle-scoring-point-details-${point.point_id}`"
                  @click.stop="togglePointDetails(point.point_id)"
                >
                  <span
                    v-if="
                      hasTeacherDraft &&
                      pointHasDraftChange(point) &&
                      (sourceType === 'model' || sourceType === 'llm')
                    "
                    class="shrink-0 font-semibold text-amber-700"
                    :data-testid="`model-draft-delta-${point.point_id}`"
                  >
                    {{ t('Model') }} {{ formatAssessmentScore(originalPointScore(point)) }} →
                    {{ t('Draft') }}
                    {{ formatAssessmentScore(pointScore(point)) }}
                  </span>
                  <span class="min-w-0 truncate text-slate-500" :title="pointReason(point)">
                    <span v-if="hasTeacherDraft && pointHasDraftChange(point)">· </span>
                    <span class="font-semibold">{{ t('Point rationale') }}</span>
                    <span v-if="pointReason(point)"> · {{ pointReason(point) }}</span>
                  </span>
                  <span class="ml-auto shrink-0 text-slate-400" aria-hidden="true">
                    {{ expandedPointId === point.point_id ? '⌃' : '⌄' }}
                  </span>
                </button>
                <div class="w-24 justify-self-end">
                  <div class="flex gap-0.5" :aria-label="t('Set scoring-point score')">
                    <button
                      v-for="segmentScore in pointScoreSegments(point)"
                      :key="segmentScore"
                      type="button"
                      class="h-2.5 flex-1 rounded-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--interaction-active)] focus-visible:ring-offset-1"
                      :class="
                        segmentScore <= pointScore(point)
                          ? 'bg-[var(--interaction-active)] hover:bg-[var(--interaction-active-strong)]'
                          : 'bg-slate-200 hover:bg-[#b7d2ce]'
                      "
                      :aria-label="`${t('Set score')} ${formatAssessmentScore(segmentScore)} / ${formatAssessmentScore(pointMaximum(point))}`"
                      :aria-pressed="segmentScore <= pointScore(point)"
                      :title="`${formatAssessmentScore(segmentScore)} / ${formatAssessmentScore(pointMaximum(point))}`"
                      :data-testid="`inline-point-score-segment-${point.point_id}-${segmentScore}`"
                      @click.stop="selectPointScoreSegment(point, segmentScore)"
                    />
                  </div>
                </div>
              </div>

              <div
                v-if="expandedPointId === point.point_id"
                class="mt-1.5 border-t border-slate-100 pt-1.5 text-[8px] leading-3.5 text-slate-600"
                :data-testid="`scoring-point-details-${point.point_id}`"
              >
                {{ pointReason(point) || t('No point rationale yet') }}
              </div>
            </div>
          </div>
          <label v-else class="flex items-center gap-2 text-[10px] font-semibold text-slate-600">
            {{ t('Criterion score') }}
            <input
              v-model.number="scoreDraft"
              type="number"
              min="0"
              :max="props.item.max_score"
              step="1"
              class="w-20 rounded-md border border-slate-200 px-2 py-1.5 text-right text-[var(--interaction-active)] focus:border-[var(--interaction-active)] focus:outline-none"
              @input="teacherDraftTouched = true"
            />
            / {{ formatAssessmentScore(props.item.max_score) }}
          </label>
        </div>

        <div class="min-w-0 border-l border-slate-200 pl-4">
          <div class="mb-2.5 min-h-12" data-testid="rationale-column-header">
            <div class="flex h-8 items-center justify-between gap-3">
              <label
                class="text-[10px] font-bold text-slate-700"
                :for="`reason-${props.item.criteria_id}`"
                >{{ t('Scoring rationale') }}</label
              >
              <div
                class="flex h-8 w-[9.5rem] shrink-0 items-stretch justify-end"
                data-testid="primary-grade-action-slot"
              >
                <button
                  v-if="props.canAcceptModelGrade && !hasTeacherDraft"
                  type="button"
                  :disabled="props.modelAcceptanceBusy"
                  :data-testid="`accept-model-pregrade-${props.item.criteria_id}`"
                  class="inline-flex h-8 w-full items-center justify-center whitespace-nowrap rounded-md border border-emerald-200 bg-emerald-50 px-3 text-[9px] font-semibold text-emerald-800 hover:bg-emerald-100 disabled:opacity-50"
                  @click.stop="emit('accept-model', props.item.criteria_id)"
                >
                  {{ props.modelAcceptanceBusy ? t('Accepting…') : t('Accept model pre-grade') }}
                </button>
                <button
                  v-else-if="hasTeacherDraft"
                  type="button"
                  :disabled="props.gradeSaving"
                  class="inline-flex h-8 w-full items-center justify-center whitespace-nowrap rounded-md bg-[var(--interaction-active)] px-3 text-[9px] font-semibold text-white hover:bg-[var(--interaction-active-strong)] disabled:cursor-not-allowed disabled:opacity-40"
                  :data-testid="`save-inline-grade-${props.item.criteria_id}`"
                  @click.stop="saveGrade"
                >
                  {{ props.gradeSaving ? t('Saving…') : t('Save teacher grade') }}
                </button>
              </div>
            </div>
            <div class="flex h-4 items-center justify-between gap-3 text-[9px]">
              <span
                v-if="hasTeacherDraft"
                class="min-w-0 truncate font-semibold text-amber-700"
                data-testid="unsaved-teacher-draft"
              >
                {{ t('Unsaved teacher draft') }} · {{ formatAssessmentScore(currentScore) }} →
                {{ formatAssessmentScore(totalScore) }}
                <template v-if="props.consistency">
                  · {{ consistencyLabel(props.consistency.state) }} ·
                  {{ consistencyPercent(props.consistency) }}
                </template>
              </span>
              <span v-else-if="props.consistency" class="min-w-0 truncate text-slate-400">
                {{ consistencyLabel(props.consistency.state) }} ·
                {{ consistencyPercent(props.consistency) }}
              </span>
              <button
                type="button"
                class="ml-auto shrink-0 whitespace-nowrap font-semibold text-[var(--interaction-active)] hover:text-[var(--interaction-active-strong)]"
                @click.stop="adjustEvidence"
              >
                {{ t(activeEvidence ? 'Reselect evidence' : 'Add evidence') }} ·
                {{ props.evidenceSegments.length }}
              </button>
            </div>
          </div>
          <textarea
            ref="reasonTextarea"
            :id="`reason-${props.item.criteria_id}`"
            v-model="reasonDraft"
            rows="1"
            class="min-h-[72px] w-full resize-none overflow-y-hidden rounded-md border border-slate-200 px-3 py-2.5 text-[10px] leading-4 text-slate-700 focus:border-[var(--interaction-active)] focus:outline-none"
            :placeholder="t('Explain the score adjustment')"
            @input="teacherDraftTouched = true"
          />
          <div v-if="props.actionError" class="mt-2 text-[9px] text-rose-700">
            {{ props.actionError }}
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="props.mode === 'conflict'" class="px-3 py-3">
      <ConflictReviewPanel
        :conflicts="props.conflicts"
        :report-name="props.reportName"
        inline
        @select-criterion="emit('update:mode', 'conflict')"
      />
    </div>

    <div v-else class="px-4 py-3" :data-testid="`inline-rubric-editor-${props.item.criteria_id}`">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-[10px] font-bold text-slate-700">
            {{ t('Criterion definition') }}
          </div>
        </div>
        <span
          class="rounded-full border px-2 py-1 text-[9px] font-semibold"
          :class="
            rubricBudgetValid
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-rose-200 bg-rose-50 text-rose-700'
          "
          data-testid="rubric-budget-status"
        >
          {{ t('Point budget') }} · {{ formatAssessmentScore(rubricBudget) }}/{{
            formatAssessmentScore(rubricDraft.max_score)
          }}
          {{ rubricBudgetValid ? '✓' : '!' }}
        </span>
      </div>

      <div class="mt-2 grid grid-cols-[minmax(12rem,1.2fr)_minmax(8rem,.7fr)_5.5rem] gap-2">
        <label class="text-[8px] font-semibold uppercase tracking-wide text-slate-400">
          {{ t('Name') }}
          <input
            v-model="rubricDraft.criteria_name"
            class="mt-1 h-8 w-full rounded-md border border-slate-200 px-2 text-[10px] normal-case tracking-normal text-slate-700 focus:border-amber-400 focus:outline-none"
          />
        </label>
        <label class="text-[8px] font-semibold uppercase tracking-wide text-slate-400">
          {{ t('Criteria category') }}
          <input
            v-model="rubricDraft.section"
            class="mt-1 h-8 w-full rounded-md border border-slate-200 px-2 text-[10px] normal-case tracking-normal text-slate-700 focus:border-amber-400 focus:outline-none"
          />
        </label>
        <label class="text-[8px] font-semibold uppercase tracking-wide text-slate-400">
          {{ t('Maximum') }}
          <input
            v-model.number="rubricDraft.max_score"
            type="number"
            min="0"
            step="1"
            class="mt-1 h-8 w-full rounded-md border border-slate-200 px-2 text-right text-[10px] normal-case tracking-normal text-slate-700 focus:border-amber-400 focus:outline-none"
          />
        </label>
      </div>
      <label class="mt-2 block text-[8px] font-semibold uppercase tracking-wide text-slate-400">
        {{ t('Description') }}
        <textarea
          v-model="rubricDraft.description"
          rows="2"
          class="mt-1 min-h-[48px] w-full resize-y rounded-md border border-slate-200 px-2.5 py-2 text-[10px] font-normal normal-case leading-4 tracking-normal text-slate-700 focus:border-amber-400 focus:outline-none"
        />
      </label>

      <div class="mt-3 border-t border-slate-200 pt-2.5">
        <div class="flex items-center justify-between gap-3">
          <div class="text-[10px] font-bold text-slate-700">
            {{ t('Scoring-point structure') }}
          </div>
          <div class="flex items-center gap-2">
            <div v-if="rubricDraft.scoring_points.length > 1" class="flex items-center gap-1.5">
              <span class="text-[8px] font-semibold uppercase tracking-wide text-slate-400">{{
                t('Allocation')
              }}</span>
              <div
                class="flex h-2 w-32 overflow-hidden rounded-full bg-slate-100"
                aria-label="Scoring-point allocation"
                data-testid="rubric-allocation-rail"
              >
                <div
                  v-for="(point, pointIndex) in rubricDraft.scoring_points"
                  :key="point.point_id"
                  class="min-w-1 border-r border-white/80 bg-[var(--interaction-active)] last:border-r-0"
                  :class="pointIndex % 2 ? 'opacity-75' : 'opacity-100'"
                  :style="{ width: rubricAllocationShare(point) }"
                  :title="`${point.summary || t('Scoring point')}: ${formatAssessmentScore(scoringPointDeductionCapacity(point))}`"
                />
              </div>
            </div>
            <button
              type="button"
              class="rounded-md border border-[#b7d2ce] bg-[var(--interaction-active-soft)] px-2 py-1 text-[8px] font-semibold text-[var(--interaction-active)] hover:bg-[#cfe4e1]"
              :data-testid="`add-rubric-point-${props.item.criteria_id}`"
              @click.stop="addScoringPoint"
            >
              ＋ {{ t('Scoring point') }}
            </button>
          </div>
        </div>

        <div v-if="rubricDraft.scoring_points.length" class="mt-2 space-y-2">
          <div
            v-for="(point, pointIndex) in rubricDraft.scoring_points"
            :key="point.point_id"
            class="rounded-md border border-slate-200 bg-slate-50/30 px-3 py-2"
            :data-testid="`rubric-point-${point.point_id}`"
          >
            <div class="grid grid-cols-[minmax(10rem,1fr)_5rem_1.75rem] items-end gap-2">
              <label class="text-[8px] font-semibold uppercase tracking-wide text-slate-400">
                {{ t('Scoring point') }}
                <input
                  :value="point.summary"
                  class="mt-1 h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-[10px] font-normal normal-case tracking-normal text-slate-700 focus:border-amber-400 focus:outline-none"
                  :data-testid="`rubric-point-content-${point.point_id}`"
                  @input="updatePointSummary(pointIndex, ($event.target as HTMLInputElement).value)"
                />
              </label>
              <label class="text-[8px] font-semibold uppercase tracking-wide text-slate-400">
                {{ t('Score') }}
                <input
                  type="number"
                  min="0"
                  step="1"
                  class="mt-1 h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-right text-[10px] font-semibold normal-case tracking-normal text-[var(--interaction-active)] focus:border-[var(--status-review)] focus:outline-none"
                  :value="scoringPointDeductionCapacity(point)"
                  :data-testid="`rubric-point-cap-${point.point_id}`"
                  @input="
                    updatePointAllocation(pointIndex, ($event.target as HTMLInputElement).value)
                  "
                />
              </label>
              <button
                type="button"
                class="mb-1 h-7 rounded border border-slate-200 bg-white text-[10px] text-slate-400 hover:border-rose-200 hover:text-rose-600"
                :aria-label="t('Remove scoring point')"
                @click.stop="removeScoringPoint(pointIndex)"
              >
                ×
              </button>
            </div>
          </div>
        </div>
        <div
          v-else
          class="mt-2 rounded-md border border-dashed border-slate-200 px-3 py-4 text-center text-[10px] text-slate-400"
        >
          {{ t('No scoring points yet.') }}
        </div>
      </div>

      <div class="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-2.5">
        <span class="text-[9px] text-amber-700">{{
          t('Saving updates the criteria file and starts consistency audit and regrading.')
        }}</span>
        <button
          type="button"
          class="ml-auto rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[9px] font-semibold text-slate-600 hover:border-[#b7d2ce] hover:text-[var(--interaction-active)]"
          :data-testid="`advanced-rubric-edit-${props.item.criteria_id}`"
          @click.stop="emit('advanced-edit', props.item.criteria_id)"
        >
          {{ t('Advanced edit') }}
        </button>
        <button
          type="button"
          class="rounded-md border border-rose-200 bg-white px-2.5 py-1.5 text-[9px] font-semibold text-rose-700 hover:bg-rose-50"
          @click.stop="emit('delete', props.item.criteria_id)"
        >
          {{ t('Delete criterion') }}
        </button>
        <button
          type="button"
          :disabled="props.rubricSaving || !rubricBudgetValid || !rubricCategoryValid"
          class="rounded-md bg-amber-500 px-3 py-1.5 text-[9px] font-semibold text-white hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-40"
          :data-testid="`save-inline-rubric-${props.item.criteria_id}`"
          @click.stop="saveRubric"
        >
          {{ props.rubricSaving ? t('Saving…') : t('Save and start audit') }}
        </button>
        <div v-if="props.actionError" class="w-full text-right text-[9px] text-rose-700">
          {{ props.actionError }}
        </div>
      </div>
    </div>

    <div
      v-if="props.mode === 'score' && (canAuthorize || authorization === 'repair_required')"
      class="flex items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/60 px-4 py-2"
    >
      <button
        v-if="canAuthorize"
        type="button"
        :disabled="props.authorizationBusy || Boolean(props.conflicts.length)"
        class="rounded-md border px-2.5 py-1 text-[9px] font-semibold disabled:opacity-50"
        :class="
          consistencyReliable
            ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
            : 'border-amber-300 bg-amber-50 text-amber-900'
        "
        @click.stop="emit('authorize', props.item.criteria_id)"
      >
        {{ props.authorizationBusy ? t('Authorizing…') : t('Authorize model grading') }}
      </button>
      <button
        v-else
        type="button"
        :disabled="props.authorizationBusy"
        class="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1 text-[9px] font-semibold text-amber-900 disabled:opacity-50"
        @click.stop="emit('confirm-repair', props.item.criteria_id)"
      >
        {{
          props.authorizationBusy ? t('Repairing…') : t('Confirm review and regrade model scores')
        }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.score-value-input {
  appearance: textfield;
  -moz-appearance: textfield;
}

.score-value-input::-webkit-inner-spin-button,
.score-value-input::-webkit-outer-spin-button {
  margin: 0;
  -webkit-appearance: none;
}
</style>
