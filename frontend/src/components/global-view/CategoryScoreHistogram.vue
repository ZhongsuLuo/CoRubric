<script setup lang="ts">
import { computed } from 'vue'
import type { GlobalReportSummary } from '../../types/schema'
import {
  buildCategoryScoreHistogram,
  type CategoryHistogramBin,
  type RubricHeatmapGroup,
} from '../../utils/globalView'
import { translate as t } from '../../i18n'
import { formatScore } from './presentation'
import type { CohortChartFocus } from './cohortChartFocus'

const props = defineProps<{
  group: RubricHeatmapGroup
  reports: GlobalReportSummary[]
  activeFocusId: string | null
  pinnedFocusId: string | null
}>()

const emit = defineEmits<{
  highlight: [focus: CohortChartFocus | null]
  toggleFocus: [focus: CohortChartFocus]
  clearFocus: []
}>()

const histogram = computed(() => buildCategoryScoreHistogram(props.group, props.reports))
const largestBinCount = computed(() => Math.max(0, ...histogram.value.bins.map((bin) => bin.count)))
const barHeight = (count: number) =>
  largestBinCount.value > 0 ? `${(count / largestBinCount.value) * 100}%` : '0%'
const binFocusId = (index: number) => `histogram-${props.group.key}-bin-${index}`
const binFocus = (bin: CategoryHistogramBin, index: number): CohortChartFocus => ({
  id: binFocusId(index),
  criterionIds: props.group.columns.map((column) => column.criterionId),
  reportIds: bin.reportIds,
})
const binLabel = (bin: CategoryHistogramBin) =>
  t('{from}–{to}: {count} reports', {
    from: formatScore(bin.from),
    to: formatScore(bin.to),
    count: bin.count,
  })
const coverageLabel = computed(() =>
  t('{count}/{total} complete · max {max}', {
    count: histogram.value.completeCount,
    total: histogram.value.totalReports,
    max: formatScore(histogram.value.maxScore),
  }),
)
const histogramLabel = computed(() =>
  t(
    'Category score distribution for {category}: {count} of {total} reports complete, maximum {max}',
    {
      category: props.group.label,
      count: histogram.value.completeCount,
      total: histogram.value.totalReports,
      max: formatScore(histogram.value.maxScore),
    },
  ),
)
</script>

<template>
  <div
    class="flex h-full min-w-0 flex-col justify-end overflow-hidden px-1 pb-0.5"
    :data-testid="`heatmap-category-histogram-${group.key}`"
  >
    <div
      class="flex min-h-0 w-full flex-1 items-end justify-center gap-px border-b border-[var(--border-subtle)]"
      role="group"
      :aria-label="histogramLabel"
    >
      <button
        v-for="(bin, index) in histogram.bins"
        :key="index"
        type="button"
        class="group/bin flex h-full min-w-0 max-w-3 flex-1 items-end justify-center rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--matrix-highlight)]"
        :class="activeFocusId === binFocusId(index) ? 'bg-[var(--interaction-active-soft)]' : ''"
        :disabled="!bin.count"
        :aria-label="binLabel(bin)"
        :aria-pressed="pinnedFocusId === binFocusId(index)"
        :title="binLabel(bin)"
        :data-bin-count="bin.count"
        :data-testid="`heatmap-category-histogram-${group.key}-bin-${index}`"
        @mouseenter="bin.count && emit('highlight', binFocus(bin, index))"
        @mouseleave="bin.count && emit('highlight', null)"
        @focus="bin.count && emit('highlight', binFocus(bin, index))"
        @blur="bin.count && emit('highlight', null)"
        @click="emit('toggleFocus', binFocus(bin, index))"
        @keydown.esc.stop="emit('clearFocus')"
      >
        <span
          class="block w-[4px] shrink-0 rounded-t-[1px] bg-[var(--interaction-active)] transition-opacity"
          :class="
            activeFocusId === binFocusId(index)
              ? 'opacity-100'
              : 'opacity-75 group-hover/bin:opacity-100 group-focus-visible/bin:opacity-100'
          "
          :style="{ height: barHeight(bin.count) }"
          :data-testid="`heatmap-category-bar-${group.key}-${index}`"
        ></span>
      </button>
    </div>
    <span
      class="truncate text-center text-[8px] leading-[10px] text-[var(--text-secondary)]"
      :data-testid="`heatmap-category-coverage-${group.key}`"
    >
      {{ coverageLabel }}
    </span>
  </div>
</template>
