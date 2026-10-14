<script setup lang="ts">
defineOptions({ name: 'CriteriaEditorModal' })

import { computed, ref, watch } from 'vue'
import { useGradingStore } from '../stores/grading'
import DiffModal from './DiffModal.vue'
import StructuredReportScoring from './StructuredReportScoring.vue'
import StructuredRubricEditor from './StructuredRubricEditor.vue'
import type { ScoringPointScore } from '../types/schema'
import {
  normalizeScoringPointScores,
  scoringPointDeductionCapacity,
  structuredPointScoreTotal,
  validateStructuredScoreBudget,
} from '../utils/scoringPoints'
import { translate as $t } from '../i18n'

const store = useGradingStore()
const props = withDefaults(defineProps<{ initialTab?: 'report' | 'criteria' }>(), {
  initialTab: 'report',
})
const emit = defineEmits<{ (event: 'close'): void }>()
const editorTab = ref<'report' | 'criteria'>(props.initialTab)

const activeCriteria = computed(() => store.activeCriteria)
const existingCategories = computed(() => [
  ...new Set(
    (store.criteriaSchema?.criteria || [])
      .map((item) => item.section?.trim())
      .filter((section): section is string => Boolean(section)),
  ),
])
const activeImplicitGrade = computed(() => store.activeImplicitGrade)
const activeReport = computed(() => store.activeReport)
const currentGrading = computed(() => {
  const report = store.gradingResults[store.activeReportId]
  const criteriaId = store.activeCriteriaId
  if (!report || !criteriaId || criteriaId === store.UNINDUCED_CRITERIA_ID) return null
  return report.criteria.find((item) => String(item.criteria_id) === String(criteriaId)) || null
})
const activeHighlight = computed(
  () => store.activeHighlights.find((item) => item.id === store.activeHighlightId) || null,
)
const allocationSuggestion = computed(() => {
  const blocker = store.alignment.global_readiness?.blockers.find(
    (item) =>
      item.kind === 'point_allocation' &&
      item.report_name === store.activeReportId &&
      String(item.criterion_id) === String(store.activeCriteriaId),
  )
  return blocker?.scoring_point_scores?.length ? blocker.scoring_point_scores : null
})
// The editable active rubric is authoritative for scoring-point edits and the next teacher grade.
const detailScoringPoints = computed(() => activeCriteria.value?.scoring_points ?? [])
const hasStructuredScoring = computed(() => detailScoringPoints.value.length > 0)
const gradingContent = ref('')
const gradingReason = ref('')
const gradingScore = ref<number | null>(null)
const selectedDeductionRuleIds = ref<string[]>([])
const scoringPointScores = ref<ScoringPointScore[]>([])
const gradeSaving = ref(false)
const acceptSaving = ref(false)
const gradeMessage = ref('')
const gradeError = ref('')

const criteriaSaving = ref(false)
const criteriaMessage = ref('')
const criteriaError = ref('')

watch(
  () => [
    currentGrading.value,
    activeHighlight.value,
    activeImplicitGrade.value,
    allocationSuggestion.value,
  ],
  ([grading, highlight]) => {
    const highlightAnchor = highlight && 'anchor' in highlight ? highlight.anchor : null
    const gradingAnchorText = grading && 'anchor_text' in grading ? grading.anchor_text : null
    const gradingFileContent =
      grading && 'file_original_content' in grading ? grading.file_original_content : ''
    if (activeImplicitGrade.value) {
      gradingContent.value =
        activeImplicitGrade.value.anchor.text ||
        activeImplicitGrade.value.anchor.image_alt ||
        activeImplicitGrade.value.anchor.image_src ||
        ''
      gradingReason.value =
        activeImplicitGrade.value.notes || activeImplicitGrade.value.temporary_label || ''
      gradingScore.value = activeImplicitGrade.value.score ?? null
      return
    }
    gradingContent.value = highlightAnchor
      ? highlightAnchor.text || ''
      : gradingAnchorText || gradingFileContent || ''
    gradingReason.value = grading && 'score_reason' in grading ? grading.score_reason || '' : ''
    gradingScore.value =
      grading && 'criteria_score' in grading ? (grading.criteria_score ?? null) : null
    selectedDeductionRuleIds.value =
      grading && 'matched_deduction_rules' in grading
        ? [...(grading.matched_deduction_rules || [])]
        : []
    scoringPointScores.value = normalizeScoringPointScores(
      detailScoringPoints.value,
      grading && 'scoring_point_scores' in grading && grading.scoring_point_scores?.length
        ? grading.scoring_point_scores
        : allocationSuggestion.value || [],
      grading && 'criteria_score' in grading ? grading.criteria_score : null,
    )
  },
  { immediate: true },
)

watch(
  () => [store.activeReportId, store.activeCriteriaId, store.activeImplicitGradeId],
  () => {
    gradeMessage.value = ''
    gradeError.value = ''
  },
)

watch(
  detailScoringPoints,
  (points) => {
    const knownRuleIds = new Set(
      points.flatMap((point) => point.deduction_rules || []).map((rule) => rule.rule_id),
    )
    selectedDeductionRuleIds.value = selectedDeductionRuleIds.value.filter((ruleId) =>
      knownRuleIds.has(ruleId),
    )
    scoringPointScores.value = normalizeScoringPointScores(
      points,
      scoringPointScores.value,
      structuredGrade.value,
    )
  },
  { deep: true },
)

const canSubmitGrade = computed(() => Boolean(activeCriteria.value && store.activeReportId))
const canAcceptModelGrade = computed(() =>
  Boolean(
    activeCriteria.value && store.activeReportId && store.canAcceptModelGrade(currentGrading.value),
  ),
)
const structuredGrade = computed(() =>
  structuredPointScoreTotal(
    normalizeScoringPointScores(detailScoringPoints.value, scoringPointScores.value),
  ),
)

const structuredMatchedPointIds = computed(() =>
  normalizeScoringPointScores(detailScoringPoints.value, scoringPointScores.value)
    .filter(
      (item) =>
        item.score <
        scoringPointDeductionCapacity(
          detailScoringPoints.value.find((point) => point.point_id === item.point_id)!,
        ),
    )
    .map((item) => item.point_id),
)

const submitGrade = async () => {
  if (!activeCriteria.value || !store.activeReportId) return
  gradeSaving.value = true
  gradeMessage.value = ''
  gradeError.value = ''
  try {
    const saveGrade = store.saveGradeDirect
      ? (payload: {
          reportId: string
          criteriaId: string
          score: number
          reason: string
          anchorText: string
          matchedScoringPoints?: string[]
          matchedDeductionRules?: string[]
          scoringPointScores?: ScoringPointScore[]
        }) => store.saveGradeDirect(payload)
      : (payload: {
          reportId: string
          criteriaId: string
          score: number
          reason: string
          anchorText: string
          matchedScoringPoints?: string[]
          matchedDeductionRules?: string[]
          scoringPointScores?: ScoringPointScore[]
        }) =>
          store.saveTeacherHighlight({
            report_id: payload.reportId,
            criteria_id: payload.criteriaId,
            score: payload.score,
            reason: payload.reason,
            anchor: { text: payload.anchorText },
            matched_scoring_points: payload.matchedScoringPoints || [],
            matched_deduction_rules: payload.matchedDeductionRules || [],
            scoring_point_scores: payload.scoringPointScores || [],
          })

    await saveGrade({
      reportId: store.activeReportId,
      criteriaId: activeCriteria.value.criteria_id,
      score: hasStructuredScoring.value ? structuredGrade.value : (gradingScore.value ?? 0),
      reason: gradingReason.value,
      anchorText: gradingContent.value,
      matchedScoringPoints: hasStructuredScoring.value ? structuredMatchedPointIds.value : [],
      matchedDeductionRules: hasStructuredScoring.value ? [] : selectedDeductionRuleIds.value,
      scoringPointScores: hasStructuredScoring.value
        ? normalizeScoringPointScores(detailScoringPoints.value, scoringPointScores.value)
        : [],
    })
    gradeMessage.value = $t('Submitted successfully')
    emit('close')
  } catch (err) {
    gradeError.value = err instanceof Error ? err.message : $t('Submission failed')
  } finally {
    gradeSaving.value = false
  }
}

const acceptModelGrade = async () => {
  if (!activeCriteria.value || !store.activeReportId || !canAcceptModelGrade.value) return
  acceptSaving.value = true
  gradeMessage.value = ''
  gradeError.value = ''
  try {
    await store.acceptModelGrade(store.activeReportId, activeCriteria.value.criteria_id)
    gradeMessage.value = $t('Auto-grade accepted')
  } catch (err) {
    gradeError.value = err instanceof Error ? err.message : $t('Failed to accept auto-grade')
  } finally {
    acceptSaving.value = false
  }
}

const submitCriteria = async () => {
  if (!store.criteriaSchema) return
  criteriaSaving.value = true
  criteriaMessage.value = ''
  criteriaError.value = ''
  try {
    const errors = store.criteriaSchema.criteria.flatMap((criterion) => {
      const criterionLabel = criterion.criteria_name.trim() || $t('Scoring criterion')
      const criterionErrors = validateStructuredScoreBudget(
        criterion.scoring_points,
        Number(criterion.max_score) || 0,
        criterion.scoring_aggregation,
      ).map((error) => `${criterionLabel}: ${error}`)
      if (!criterion.section?.trim()) {
        criterionErrors.unshift(`${criterionLabel}: ${$t('Every criterion needs a category')}`)
      }
      return criterionErrors
    })
    if (errors.length) {
      criteriaError.value = errors.join('; ')
      return
    }
    await store.saveCriteriaFile()
    criteriaMessage.value = $t('Rubric updated')
    emit('close')
  } catch (err) {
    criteriaError.value = err instanceof Error ? err.message : $t('Update failed')
  } finally {
    criteriaSaving.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      data-testid="criteria-editor-modal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="criteria-editor-title"
      @mousedown.self="emit('close')"
      @keydown.esc="emit('close')"
    >
      <section
        class="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"
      >
        <header class="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div class="min-w-0">
            <div
              class="text-xs font-semibold uppercase tracking-wide text-[var(--interaction-active)]"
            >
              {{ $t('Editor') }}
            </div>
            <h2
              id="criteria-editor-title"
              class="mt-1 truncate text-lg font-semibold text-slate-900"
            >
              {{
                activeCriteria?.criteria_name ||
                activeImplicitGrade?.temporary_label ||
                $t('Uninduced grading')
              }}
            </h2>
            <p class="mt-1 truncate text-xs text-slate-500">
              {{ activeReport?.name || $t('No report selected') }}
            </p>
          </div>
          <button
            type="button"
            data-testid="close-criteria-editor"
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            :aria-label="$t('Close editor')"
            @click="emit('close')"
          >
            <svg
              class="h-5 w-5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path stroke-linecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        <nav
          class="flex gap-1 border-b border-slate-200 bg-slate-50 px-6 pt-3"
          :aria-label="$t('Editor sections')"
        >
          <button
            type="button"
            data-testid="editor-tab-report"
            class="border-b-2 px-4 py-2.5 text-sm font-semibold transition-colors"
            :class="
              editorTab === 'report'
                ? 'border-[var(--interaction-active)] text-[var(--interaction-active)]'
                : 'border-transparent text-slate-500 hover:text-slate-800'
            "
            @click="editorTab = 'report'"
          >
            {{ $t('Current report') }}
          </button>
          <button
            type="button"
            data-testid="editor-tab-criteria"
            class="border-b-2 px-4 py-2.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            :class="
              editorTab === 'criteria'
                ? 'border-[var(--interaction-active)] text-[var(--interaction-active)]'
                : 'border-transparent text-slate-500 hover:text-slate-800'
            "
            :disabled="!activeCriteria"
            @click="editorTab = 'criteria'"
          >
            {{ $t('Criteria') }}
          </button>
        </nav>

        <main class="min-h-0 flex-1 overflow-y-auto px-6">
          <!-- Grading Section -->
          <div v-show="editorTab === 'report'" class="py-6" data-testid="editor-report-panel">
            <div class="mb-4 flex items-center justify-between">
              <h3 class="font-bold text-slate-800">
                {{ $t('Current Assessment') }}
              </h3>
              <div
                data-testid="detail-context"
                class="text-xs font-medium text-slate-500 bg-slate-100 rounded-full px-2 py-0.5 max-w-[50%] truncate"
              >
                {{ activeReport?.name || $t('No report selected') }} ·
                {{ activeCriteria?.criteria_name || $t('Uninduced grading') }}
              </div>
            </div>

            <div
              v-if="activeImplicitGrade"
              class="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"
            >
              <div class="font-semibold">
                {{ $t('Implicit teacher grade') }}
              </div>
              <div v-if="activeImplicitGrade.failure_reason" class="mt-1 text-rose-700">
                {{ activeImplicitGrade.failure_reason }}
              </div>
            </div>

            <div class="space-y-4">
              <div class="space-y-1.5">
                <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                  $t('Evidence / Context')
                }}</label>
                <textarea
                  v-model="gradingContent"
                  rows="3"
                  class="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-sm text-slate-800 transition-all focus:border-[var(--interaction-active)] focus:bg-white focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
                  :placeholder="$t('No derived context available.')"
                ></textarea>
              </div>

              <div class="space-y-1.5">
                <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                  $t('Reasoning')
                }}</label>
                <textarea
                  v-model="gradingReason"
                  rows="3"
                  class="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
                  :placeholder="$t('Explain the score...')"
                ></textarea>
              </div>

              <StructuredReportScoring
                v-if="activeCriteria && hasStructuredScoring"
                v-model:point-scores="scoringPointScores"
                :points="detailScoringPoints"
                :max-score="activeCriteria.max_score"
              />
              <div
                v-if="allocationSuggestion"
                class="rounded-lg border border-[#b7d2ce] bg-[var(--interaction-active-soft)] px-3 py-2 text-xs text-[var(--interaction-active-strong)]"
                data-testid="point-allocation-suggestion"
              >
                {{
                  $t(
                    'Model-suggested scoring-point allocation · Needs confirmation. Review or edit the values above, then submit to make them authoritative.',
                  )
                }}
              </div>

              <div v-if="!hasStructuredScoring" class="space-y-1.5">
                <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                  $t('Score')
                }}</label>
                <div class="flex items-center gap-3">
                  <input
                    v-model.number="gradingScore"
                    type="number"
                    data-testid="criterion-score-input"
                    class="w-24 rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold text-slate-800 transition-all focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
                    :placeholder="$t('Score')"
                  />
                  <span v-if="activeCriteria" class="text-sm text-slate-400"
                    >/ {{ activeCriteria.max_score || $t('Max') }}</span
                  >
                  <span v-else-if="activeImplicitGrade" class="text-sm text-slate-400"
                    >/ {{ activeImplicitGrade.max_score || $t('uninduced') }}</span
                  >
                </div>
              </div>

              <div class="flex items-center justify-between pt-2">
                <div class="flex flex-col">
                  <div
                    v-if="gradeMessage"
                    class="text-xs font-medium text-emerald-600 flex items-center gap-1"
                  >
                    <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                    {{ gradeMessage }}
                  </div>
                  <div
                    v-if="gradeError"
                    class="text-xs font-medium text-rose-600 flex items-center gap-1"
                  >
                    <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M6 18L18 6M6 6l12 12"
                      />
                    </svg>
                    {{ gradeError }}
                  </div>
                </div>

                <div class="flex items-center gap-2">
                  <button
                    v-if="canAcceptModelGrade"
                    data-testid="accept-model-grade"
                    class="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition-all hover:bg-emerald-100 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
                    :disabled="acceptSaving || gradeSaving"
                    @click="acceptModelGrade"
                  >
                    {{ acceptSaving ? $t('Accepting…') : $t('Accept Auto Grade') }}
                  </button>
                  <button
                    data-testid="save-teacher-grade"
                    class="rounded-lg bg-[var(--interaction-active)] px-5 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-[var(--interaction-active-strong)] focus:outline-none focus:ring-2 focus:ring-[var(--interaction-active)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
                    :disabled="gradeSaving || acceptSaving || !canSubmitGrade"
                    @click="submitGrade"
                  >
                    {{ gradeSaving ? $t('Saving...') : $t('Submit Grade') }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Criteria Definition Section -->
          <div
            v-if="activeCriteria"
            v-show="editorTab === 'criteria'"
            class="py-6"
            data-testid="editor-criteria-panel"
          >
            <div class="mb-4">
              <h3 class="font-bold text-slate-700">
                {{ $t('Criteria Definition') }}
              </h3>
            </div>

            <div class="grid gap-4">
              <div class="grid grid-cols-3 gap-4">
                <div class="space-y-1.5">
                  <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                    $t('Name')
                  }}</label>
                  <input
                    v-model="activeCriteria.criteria_name"
                    class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm transition-all focus:border-[var(--interaction-active)] focus:ring-1 focus:ring-[var(--interaction-active)] outline-none"
                  />
                </div>
                <div class="space-y-1.5">
                  <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                    $t('Criteria category')
                  }}</label>
                  <input
                    v-model="activeCriteria.section"
                    list="criteria-category-options"
                    class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm transition-all focus:border-[var(--interaction-active)] focus:ring-1 focus:ring-[var(--interaction-active)] outline-none"
                  />
                  <datalist id="criteria-category-options">
                    <option
                      v-for="category in existingCategories"
                      :key="category"
                      :value="category"
                    />
                  </datalist>
                </div>
                <div class="space-y-1.5">
                  <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                    $t('Maximum score')
                  }}</label>
                  <input
                    v-model.number="activeCriteria.max_score"
                    type="number"
                    min="0"
                    class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm transition-all focus:border-[var(--interaction-active)] focus:ring-1 focus:ring-[var(--interaction-active)] outline-none"
                  />
                </div>
              </div>

              <div class="space-y-1.5">
                <label class="text-xs font-semibold uppercase tracking-wider text-slate-500">{{
                  $t('Description')
                }}</label>
                <textarea
                  v-model="activeCriteria.description"
                  rows="4"
                  class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm transition-all focus:border-[var(--interaction-active)] focus:ring-1 focus:ring-[var(--interaction-active)] outline-none"
                ></textarea>
              </div>

              <StructuredRubricEditor
                v-model="activeCriteria.scoring_points"
                :criterion-id="activeCriteria.criteria_id"
                :max-score="activeCriteria.max_score"
              />
            </div>

            <div class="flex items-center justify-end space-x-2 pt-2">
              <div v-if="criteriaMessage" class="text-xs font-medium text-emerald-600">
                {{ criteriaMessage }}
              </div>
              <div v-if="criteriaError" class="text-xs font-medium text-rose-600">
                {{ criteriaError }}
              </div>
              <button
                class="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2 transition-all disabled:opacity-50"
                :disabled="criteriaSaving"
                @click="submitCriteria"
              >
                {{ criteriaSaving ? $t('Saving...') : $t('Update Criteria') }}
              </button>
            </div>
          </div>
        </main>

        <DiffModal
          :visible="store.diffVisible"
          :before="store.descriptionDiff?.before || ''"
          :after="store.descriptionDiff?.after || ''"
          @close="store.closeDiff"
        />
      </section>
    </div>
  </Teleport>
</template>
