<script setup lang="ts">
import { computed, ref } from 'vue'
import { useGradingStore } from '../../stores/grading'
import CohortSummary from './CohortSummary.vue'
import CohortOverview from './CohortOverview.vue'
import AutoGradeBatchBanner from './AutoGradeBatchBanner.vue'
import ReportOverview from './ReportOverview.vue'
import { useCohortSynthesis } from './useCohortSynthesis'
import { useGlobalViewData } from './useGlobalViewData'
import { isTerminalAutoGradeBatch } from '../../utils/autoGradeBatch'
import { translate as t } from '../../i18n'
import type { CohortChartFocus } from './cohortChartFocus'

const store = useGradingStore()

const batchActionPending = ref(false)
const batchActionError = ref('')
const hoveredChartFocus = ref<CohortChartFocus | null>(null)
const pinnedChartFocus = ref<CohortChartFocus | null>(null)
const {
  criterionNames,
  criterionMaxScores,
  summaries,
  summariesById,
  rubricOrderedSummaries,
  heatmapGroups,
  selectedCriterionId,
  selectedCriterionName,
  reportSummaries,
  overallReports,
} = useGlobalViewData()

const caseId = computed(() => store.activeCaseId)
const criteriaFileName = computed(() => store.activeCriteriaFile)
const projectionHash = computed(() => store.alignment.global_projection_hash)
const activeBatch = computed(() => store.activeAutoGradeBatch)
const synthesisEnabled = computed(
  () => !activeBatch.value || isTerminalAutoGradeBatch(activeBatch.value.status),
)
const overallCriterionId = computed<string | null>(() => null)
const overallSynthesisState = useCohortSynthesis({
  caseId,
  criteriaFileName,
  criterionId: overallCriterionId,
  projectionHash,
  enabled: synthesisEnabled,
})
const criterionSynthesisEnabled = computed(
  () => synthesisEnabled.value && Boolean(selectedCriterionId.value),
)
const criterionSynthesisState = useCohortSynthesis({
  caseId,
  criteriaFileName,
  criterionId: selectedCriterionId,
  projectionHash,
  enabled: criterionSynthesisEnabled,
})
const activeSynthesisState = computed(() =>
  selectedCriterionId.value ? criterionSynthesisState : overallSynthesisState,
)
const activeSynthesis = computed(() => activeSynthesisState.value.synthesis.value)
const activeSynthesisLoading = computed(() => activeSynthesisState.value.loading.value)
const activeSynthesisError = computed(() => activeSynthesisState.value.error.value)
const activeChartFocus = computed(() => hoveredChartFocus.value || pinnedChartFocus.value)
const highlightChartFocus = (focus: CohortChartFocus | null) => {
  hoveredChartFocus.value = focus
}
const toggleChartFocus = (focus: CohortChartFocus) => {
  pinnedChartFocus.value = pinnedChartFocus.value?.id === focus.id ? null : focus
  hoveredChartFocus.value = null
}
const clearChartFocus = () => {
  hoveredChartFocus.value = null
  pinnedChartFocus.value = null
}
const selectOverviewCell = (payload: { reportId: string; criterionId: string }) => {
  clearChartFocus()
  const point = summariesById.value
    .get(payload.criterionId)
    ?.points.find((item) => item.report_id === payload.reportId)
  store.setGlobalSelectedCriterion(payload.criterionId)
  store.setGlobalFocusedReport(point ? payload.reportId : null)
}
const selectOverviewCriterion = (criterionId: string | null) => {
  clearChartFocus()
  store.setGlobalSelectedCriterion(
    criterionId && criterionId === selectedCriterionId.value ? null : criterionId,
  )
  store.setGlobalFocusedReport(null)
}
const confirmBatch = async () => {
  if (!activeBatch.value || batchActionPending.value) return
  batchActionPending.value = true
  batchActionError.value = ''
  try {
    await store.confirmAutoGradeBatch(activeBatch.value.batch_id)
  } catch (error) {
    batchActionError.value =
      error instanceof Error ? error.message : t('Could not start auto-grading')
  } finally {
    batchActionPending.value = false
  }
}
const cancelBatch = async () => {
  if (!activeBatch.value || batchActionPending.value) return
  if (
    !window.confirm(
      t(
        'Stop auto-grading? The report currently being processed may finish, but no new report will start.',
      ),
    )
  ) {
    return
  }
  batchActionPending.value = true
  batchActionError.value = ''
  try {
    await store.cancelAutoGradeBatch(activeBatch.value.batch_id)
  } catch (error) {
    batchActionError.value =
      error instanceof Error ? error.message : t('Could not stop auto-grading')
  } finally {
    batchActionPending.value = false
  }
}
</script>

<template>
  <div
    class="global-view-surface h-full min-h-0 overflow-y-auto"
    data-testid="global-view"
    @keydown.esc.stop="clearChartFocus"
  >
    <AutoGradeBatchBanner
      v-if="activeBatch"
      :batch="activeBatch"
      :action-pending="batchActionPending"
      :error="batchActionError"
      @confirm="confirmBatch"
      @cancel="cancelBatch"
    />
    <section
      class="grid min-h-[360px] grid-cols-1 gap-3 px-5 py-3 xl:h-[clamp(360px,44vh,460px)] xl:grid-cols-[minmax(0,1fr)_minmax(520px,1fr)] xl:items-stretch"
      data-testid="global-analysis-workspace"
    >
      <CohortOverview
        :reports="overallReports"
        :groups="heatmapGroups"
        :criterion-names="criterionNames"
        :criterion-max-scores="criterionMaxScores"
        :selected-criterion-id="selectedCriterionId"
        :highlighted-report-ids="activeChartFocus?.reportIds || []"
        :highlighted-criterion-ids="activeChartFocus?.criterionIds || []"
        :active-focus-id="activeChartFocus?.id || null"
        :pinned-focus-id="pinnedChartFocus?.id || null"
        @select-cell="selectOverviewCell"
        @select-criterion="selectOverviewCriterion"
        @highlight="highlightChartFocus"
        @toggle-focus="toggleChartFocus"
        @clear-focus="clearChartFocus"
      />
      <CohortSummary
        :selected-criterion-id="selectedCriterionId"
        :selected-criterion-name="selectedCriterionName"
        :report-count="reportSummaries.length"
        :report-summaries="reportSummaries"
        :summaries="rubricOrderedSummaries"
        :criterion-names="criterionNames"
        :analysis="activeSynthesis?.analysis || ''"
        :insights="activeSynthesis?.insights || []"
        :synthesis-loading="activeSynthesisLoading"
        :synthesis-error="activeSynthesisError"
        :active-focus-id="activeChartFocus?.id || null"
        :pinned-focus-id="pinnedChartFocus?.id || null"
        @highlight="highlightChartFocus"
        @toggle-focus="toggleChartFocus"
        @clear-focus="clearChartFocus"
      />
    </section>
    <section
      class="mx-5 mb-5 flex min-h-[520px] flex-col overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-white"
      data-testid="global-analysis-detail"
    >
      <div class="flex min-h-[520px] flex-1">
        <ReportOverview
          class="flex-1"
          :summaries="summaries"
          :report-summaries="reportSummaries"
          :criterion-max-scores="criterionMaxScores"
          :selected-criterion-id="selectedCriterionId"
          :highlighted-report-ids="activeChartFocus?.reportIds || []"
          :highlighted-criterion-ids="activeChartFocus?.criterionIds || []"
        />
      </div>
    </section>
  </div>
</template>
