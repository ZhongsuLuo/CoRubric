<script setup lang="ts">
import type { GlobalReportSummary } from '../../types/schema'
import type { RubricHeatmapGroup } from '../../utils/globalView'
import RubricHeatmap from './RubricHeatmap.vue'
import { translate as t } from '../../i18n'
import type { CohortChartFocus } from './cohortChartFocus'

defineProps<{
  reports: GlobalReportSummary[]
  groups: RubricHeatmapGroup[]
  criterionNames: Record<string, string>
  criterionMaxScores: Record<string, number>
  selectedCriterionId: string | null
  highlightedReportIds: string[]
  highlightedCriterionIds: string[]
  activeFocusId: string | null
  pinnedFocusId: string | null
}>()

const emit = defineEmits<{
  selectCell: [payload: { reportId: string; criterionId: string }]
  selectCriterion: [criterionId: string | null]
  highlight: [focus: CohortChartFocus | null]
  toggleFocus: [focus: CohortChartFocus]
  clearFocus: []
}>()
</script>

<template>
  <section
    class="flex min-h-[520px] min-w-0 flex-col xl:h-full xl:min-h-0"
    data-testid="global-overall-overview"
    :aria-label="t('Cohort overview')"
  >
    <RubricHeatmap
      :reports="reports"
      :groups="groups"
      :criterion-names="criterionNames"
      :criterion-max-scores="criterionMaxScores"
      :selected-criterion-id="selectedCriterionId"
      :highlighted-report-ids="highlightedReportIds"
      :highlighted-criterion-ids="highlightedCriterionIds"
      :active-focus-id="activeFocusId"
      :pinned-focus-id="pinnedFocusId"
      @select="emit('selectCell', $event)"
      @criterion-select="emit('selectCriterion', $event)"
      @highlight="emit('highlight', $event)"
      @toggle-focus="emit('toggleFocus', $event)"
      @clear-focus="emit('clearFocus')"
    />
  </section>
</template>
