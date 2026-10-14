<script setup lang="ts">
import { computed } from 'vue'
import type { CriterionGlobalSummary, GlobalReportSummary } from '../../types/schema'
import type { RubricHeatmapGroup } from '../../utils/globalView'
import { gradingMatrixVisualConfig } from '../../config/gradingVisualConfig'
import { formatScore } from './presentation'
import { translate as $t } from '../../i18n'
import CategoryScoreHistogram from './CategoryScoreHistogram.vue'
import type { CohortChartFocus } from './cohortChartFocus'

const props = defineProps<{
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
  select: [payload: { reportId: string; criterionId: string }]
  criterionSelect: [criterionId: string | null]
  highlight: [focus: CohortChartFocus | null]
  toggleFocus: [focus: CohortChartFocus]
  clearFocus: []
}>()

const columns = computed(() => props.groups.flatMap((group) => group.columns))
const groupLayouts = computed(() => {
  let firstColumn = 2
  return props.groups.map((group) => {
    const layout = { group, firstColumn }
    firstColumn += group.columns.length
    return layout
  })
})

const criterionName = (criterionId: string) => props.criterionNames[criterionId] || criterionId
const criterionDetails = (summary: CriterionGlobalSummary) =>
  `${$t('Average score')} ${formatScore(summary.average_score)}/${formatScore(summary.max_score)} · ${$t('Average points lost')} ${formatScore(summary.average_loss)} · ${summary.statistical_count ?? summary.scored_count}/${summary.total_reports} ${$t('Scored reports')}`
const tooltipAlignment = (index: number) => {
  if (index === 0) return 'left-0'
  if (index === columns.value.length - 1) return 'right-0'
  return 'left-1/2 -translate-x-1/2'
}
const highlightedReportIdSet = computed(() => new Set(props.highlightedReportIds))
const highlightedCriterionIdSet = computed(() => new Set(props.highlightedCriterionIds))
const highlightsCell = (reportId: string, criterionId: string) =>
  highlightedReportIdSet.value.has(reportId) && highlightedCriterionIdSet.value.has(criterionId)

const scoreState = (report: GlobalReportSummary, criterionId: string) =>
  report.criteria_scores[criterionId]
const lossRatio = (report: GlobalReportSummary, criterionId: string) => {
  const state = scoreState(report, criterionId)
  if (
    !state ||
    state.status === 'not_graded' ||
    state.score === null ||
    state.score === undefined
  ) {
    return null
  }
  const score = Number(state.score)
  const maximum = Number(state.max_score ?? props.criterionMaxScores[criterionId])
  if (!Number.isFinite(score) || !Number.isFinite(maximum) || maximum <= 0) return null
  return Math.max(0, Math.min(1, (maximum - score) / maximum))
}
const cellClass = (report: GlobalReportSummary, criterionId: string) => {
  const state = scoreState(report, criterionId)
  if (state?.status === 'conflict') return gradingMatrixVisualConfig.conflict
  const ratio = lossRatio(report, criterionId)
  if (ratio === null) return gradingMatrixVisualConfig.ungraded
  if (ratio <= 0.001) return gradingMatrixVisualConfig.full
  if (ratio <= 0.25) return gradingMatrixVisualConfig.lowLoss
  if (ratio <= 0.5) return gradingMatrixVisualConfig.mediumLoss
  if (ratio <= 0.75) return gradingMatrixVisualConfig.highLoss
  return gradingMatrixVisualConfig.severeLoss
}
const cellScore = (report: GlobalReportSummary, criterionId: string) => {
  const state = scoreState(report, criterionId)
  if (state?.status === 'conflict') return $t('conflict')
  if (state?.score === null || state?.score === undefined) return $t('not graded')
  return `${formatScore(state.score)}/${formatScore(
    state.max_score ?? props.criterionMaxScores[criterionId],
  )}`
}
const cellLabel = (report: GlobalReportSummary, criterionId: string) =>
  `${report.report_name}, ${criterionName(criterionId)}: ${cellScore(report, criterionId)}`
</script>

<template>
  <div
    class="global-panel flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border shadow-sm"
    data-testid="global-rubric-heatmap"
  >
    <div
      class="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto"
      data-testid="heatmap-scroll-region"
    >
      <div
        class="grid min-h-full w-full min-w-0 content-stretch gap-px p-1.5"
        data-testid="heatmap-grid"
        :style="{
          gridTemplateColumns: `40px repeat(${columns.length}, minmax(0, 1fr)) 24px`,
          gridTemplateRows: `24px 50px 30px repeat(${reports.length}, minmax(16px, 1fr))`,
        }"
      >
        <div
          class="sticky left-0 top-[74px] z-40 flex items-center justify-center bg-[var(--surface-panel)] text-slate-400"
          data-testid="heatmap-report-header"
          :style="{ gridColumn: '1', gridRow: '3' }"
        >
          <button
            v-if="selectedCriterionId"
            type="button"
            class="interaction-text flex h-full w-full items-center justify-center text-sm font-medium hover:text-[var(--interaction-active-strong)]"
            data-testid="heatmap-clear-selection"
            :aria-label="$t('Back to overall')"
            :title="$t('Back to overall')"
            @click="emit('criterionSelect', null)"
          >
            ←
          </button>
          <span v-else class="truncate text-[9px] font-semibold uppercase tracking-wide">
            {{ $t('Report') }}
          </span>
        </div>
        <div
          v-for="{ group, firstColumn } in groupLayouts"
          :key="`category-${group.key}`"
          class="sticky top-0 z-30 flex min-w-0 items-center justify-center overflow-hidden border-b border-[var(--border-subtle)] bg-[var(--surface-panel)] px-1"
          :data-testid="`heatmap-category-${group.key}`"
          :data-category="group.label"
          :title="group.label"
          :style="{
            gridColumn: `${firstColumn} / span ${group.columns.length}`,
            gridRow: '1',
          }"
        >
          <span class="truncate text-[9px] font-semibold text-[var(--text-primary)]">
            {{ group.label }}
          </span>
        </div>
        <div
          v-for="{ group, firstColumn } in groupLayouts"
          :key="`histogram-${group.key}`"
          class="sticky top-6 z-30 min-w-0 bg-[var(--surface-panel)]"
          :style="{
            gridColumn: `${firstColumn} / span ${group.columns.length}`,
            gridRow: '2',
          }"
        >
          <CategoryScoreHistogram
            :group="group"
            :reports="reports"
            :active-focus-id="activeFocusId"
            :pinned-focus-id="pinnedFocusId"
            @highlight="emit('highlight', $event)"
            @toggle-focus="emit('toggleFocus', $event)"
            @clear-focus="emit('clearFocus')"
          />
        </div>
        <button
          v-for="(column, index) in columns"
          :key="`heading-${column.criterionId}`"
          type="button"
          class="group relative sticky top-[74px] z-30 flex min-w-0 items-center justify-center overflow-visible rounded bg-[var(--surface-panel)] transition-colors hover:z-50 hover:bg-[var(--interaction-active-soft)] focus-visible:z-50"
          :class="
            selectedCriterionId === column.criterionId ||
            highlightedCriterionIdSet.has(column.criterionId)
              ? 'interaction-soft interaction-outline'
              : ''
          "
          :data-testid="`heatmap-criterion-${column.criterionId}`"
          :data-criterion-id="column.criterionId"
          :style="{ gridColumn: `${index + 2}`, gridRow: '3' }"
          :aria-label="
            $t('Open {name} analysis', {
              name: criterionName(column.criterionId),
            })
          "
          :title="
            $t('{name} · −{loss} average loss', {
              name: criterionName(column.criterionId),
              loss: formatScore(column.summary.average_loss),
            })
          "
          @click="emit('criterionSelect', column.criterionId)"
        >
          <span
            class="text-[10px] font-semibold tabular-nums text-[var(--text-secondary)] group-hover:text-[var(--interaction-active)]"
            :data-testid="`heatmap-code-${column.criterionId}`"
          >
            {{ column.code }}
          </span>
          <span
            class="pointer-events-none absolute top-full z-50 mt-1 hidden w-52 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-panel)] p-2 text-left shadow-lg group-hover:block group-focus-visible:block"
            :class="tooltipAlignment(index)"
            :data-testid="`heatmap-tooltip-${column.criterionId}`"
          >
            <span class="block text-[10px] font-semibold text-[var(--text-primary)]">
              <span class="mr-1 text-[var(--interaction-active)]">{{ column.code }}</span>
              <span :data-testid="`heatmap-name-${column.criterionId}`">
                {{ criterionName(column.criterionId) }}
              </span>
            </span>
            <span class="mt-1 block text-[9px] leading-4 text-[var(--text-secondary)]">
              {{ criterionDetails(column.summary) }}
            </span>
          </span>
        </button>
        <div
          class="sticky top-0 z-30 flex items-end justify-center bg-[var(--surface-panel)] pb-1 text-[9px] font-semibold uppercase text-slate-400"
          :style="{ gridColumn: `${columns.length + 2}`, gridRow: '1 / span 3' }"
        >
          Score
        </div>
        <template v-for="(report, reportIndex) in reports" :key="report.report_id">
          <div
            class="sticky left-0 z-20 flex min-w-0 items-center bg-[var(--surface-panel)] pr-1 text-[10px] font-medium"
            :class="
              highlightedReportIdSet.has(report.report_id) ? 'interaction-text' : 'text-slate-600'
            "
            :data-testid="`heatmap-report-label-${report.report_id}`"
            :title="report.report_name"
            :style="{ gridColumn: '1', gridRow: `${reportIndex + 4}` }"
          >
            <span class="truncate">{{ report.report_name }}</span>
          </div>
          <button
            v-for="(column, criterionIndex) in columns"
            :key="`${report.report_id}-${column.criterionId}`"
            type="button"
            class="h-full min-h-[10px] w-full rounded-[1px] border transition-colors hover:relative hover:z-10 hover:ring-2 hover:ring-inset hover:ring-[var(--matrix-highlight)] focus:relative focus:z-10 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[var(--matrix-highlight)]"
            :class="[
              cellClass(report, column.criterionId),
              highlightsCell(report.report_id, column.criterionId)
                ? 'relative z-10 ring-2 ring-inset ring-[var(--matrix-highlight)]'
                : '',
            ]"
            :data-testid="`heatmap-cell-${report.report_id}-${column.criterionId}`"
            :aria-label="cellLabel(report, column.criterionId)"
            :title="cellLabel(report, column.criterionId)"
            :style="{
              gridColumn: `${criterionIndex + 2}`,
              gridRow: `${reportIndex + 4}`,
            }"
            @click="
              emit('select', {
                reportId: report.report_id,
                criterionId: column.criterionId,
              })
            "
          ></button>
          <div
            class="flex items-center justify-center text-[8px] font-semibold tabular-nums text-slate-500"
            :style="{
              gridColumn: `${columns.length + 2}`,
              gridRow: `${reportIndex + 4}`,
            }"
          >
            {{ formatScore(report.score) }}
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.heatmap-cell-ungraded {
  background-image: repeating-linear-gradient(
    135deg,
    transparent 0 3px,
    rgb(203 213 225 / 0.75) 3px 4.5px
  );
}

.heatmap-cell-conflict {
  position: relative;
  background-image: repeating-linear-gradient(
    135deg,
    transparent 0 3px,
    rgb(163 61 70 / 0.22) 3px 4.5px
  );
}

.heatmap-cell-conflict::after {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--status-conflict);
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
}

.heatmap-cell-conflict::after {
  content: '!';
}
</style>
