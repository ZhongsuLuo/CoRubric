<script setup lang="ts">
import { computed } from 'vue'
import type {
  CohortSynthesisInsight,
  CriterionGlobalSummary,
  GlobalReportSummary,
} from '../../types/schema'
import { translate as $t } from '../../i18n'
import { formatScore } from './presentation'
import type { CohortChartFocus } from './cohortChartFocus'

type ChartRow = CohortChartFocus & {
  label: string
  averageLoss: number | null
  affectedCount: number
  coveredCount: number
  evidenceCount: number
}
type DeterministicChartRow = ChartRow & { averageLoss: number }

const averageLossSuffixPattern =
  /(?:[：:]\s*平均失分\s*-?\d+(?:\.\d+)?\s*分?|\s+average\s+loss\s+-?\d+(?:\.\d+)?)\s*$/iu
const chartLabel = (label: string) => label.replace(averageLossSuffixPattern, '').trim() || label

const props = defineProps<{
  selectedCriterionId: string | null
  selectedCriterionName: string
  reportCount: number
  reportSummaries: GlobalReportSummary[]
  summaries: CriterionGlobalSummary[]
  criterionNames: Record<string, string>
  analysis: string
  insights: CohortSynthesisInsight[]
  synthesisLoading: boolean
  synthesisError: string
  activeFocusId: string | null
  pinnedFocusId: string | null
}>()

const emit = defineEmits<{
  highlight: [focus: CohortChartFocus | null]
  toggleFocus: [focus: CohortChartFocus]
  clearFocus: []
}>()

const selectedSummary = computed(() =>
  props.selectedCriterionId
    ? props.summaries.find((summary) => summary.criterion_id === props.selectedCriterionId) || null
    : null,
)
const scoredReportScores = computed(() =>
  props.reportSummaries.flatMap((report) => {
    if (report.score === null || report.score === undefined) return []
    const score = Number(report.score)
    return Number.isFinite(score) ? [score] : []
  }),
)
const averageReportScore = computed(() => {
  if (!scoredReportScores.value.length) return null
  return (
    scoredReportScores.value.reduce((sum, score) => sum + score, 0) /
    scoredReportScores.value.length
  )
})
const completeReportCount = computed(
  () => props.reportSummaries.filter((report) => !report.partial).length,
)
const totalConflictCount = computed(() =>
  props.reportSummaries.reduce((sum, report) => sum + report.conflict_count, 0),
)
const sourceSummaries = computed(() =>
  selectedSummary.value ? [selectedSummary.value] : props.summaries,
)
const teacherCount = computed(() =>
  sourceSummaries.value.reduce((sum, summary) => sum + summary.teacher_count, 0),
)
const modelCount = computed(() =>
  sourceSummaries.value.reduce((sum, summary) => sum + summary.model_count, 0),
)
const affectedReportIdsFor = (summary: CriterionGlobalSummary) =>
  Array.from(
    new Set(
      summary.points
        .filter(
          (point) =>
            point.included_in_statistics !== false &&
            !point.conflict &&
            Number(point.score) < Number(point.max_score),
        )
        .map((point) => point.report_id),
    ),
  )
const deterministicChartRows = computed<DeterministicChartRow[]>(() => {
  const summary = selectedSummary.value
  if (summary) {
    return [...(summary.point_loss_summaries || [])].map((point) => ({
      id: point.point_id,
      criterionIds: [summary.criterion_id],
      reportIds: point.affected_report_ids,
      label: point.summary || $t('Scoring point'),
      averageLoss: Number(point.average_loss) || 0,
      affectedCount: point.affected_report_count,
      coveredCount: point.covered_report_count,
      evidenceCount: 0,
    }))
  }
  return props.summaries.map((summary) => {
    const reportIds = affectedReportIdsFor(summary)
    return {
      id: summary.criterion_id,
      criterionIds: [summary.criterion_id],
      reportIds,
      label: props.criterionNames[summary.criterion_id] || $t('Scoring criterion'),
      averageLoss: Number(summary.average_loss) || 0,
      affectedCount: reportIds.length,
      coveredCount: summary.statistical_count ?? summary.scored_count,
      evidenceCount: 0,
    }
  })
})
const synthesisChartRows = computed<ChartRow[]>(() =>
  props.insights
    .map((insight) => ({
      id: insight.insight_id,
      criterionIds:
        insight.criterion_ids.length || !props.selectedCriterionId
          ? insight.criterion_ids
          : [props.selectedCriterionId],
      reportIds: insight.report_ids,
      label: chartLabel(insight.summary),
      averageLoss: null,
      affectedCount: new Set(insight.report_ids).size,
      coveredCount: props.reportCount,
      evidenceCount: new Set(
        insight.supporting_refs
          .map((reference) => {
            const reportId = String(reference.report_id || '')
            const eventId = String(reference.event_id || '')
            return reportId && eventId ? `${reportId}::${eventId}` : null
          })
          .filter(Boolean),
      ).size,
    }))
    .sort((left, right) => right.affectedCount - left.affectedCount)
    .slice(0, 4),
)
const chartRows = computed(() =>
  synthesisChartRows.value.length ? synthesisChartRows.value : deterministicChartRows.value,
)
const dominantLossRow = computed(() => {
  let dominant = deterministicChartRows.value[0] || null
  for (const row of deterministicChartRows.value.slice(1)) {
    if (dominant && row.averageLoss <= dominant.averageLoss) continue
    dominant = row
  }
  return dominant
})
const fallbackAnalysisText = computed(() => {
  const summary = selectedSummary.value
  if (summary) {
    return $t(
      '{scored} of {total} reports contribute to {criterion}. The average score is {average}/{maximum}, with {loss} points lost on average.',
      {
        scored: summary.statistical_count ?? summary.scored_count,
        total: summary.total_reports,
        criterion: props.selectedCriterionName,
        average: formatScore(summary.average_score),
        maximum: formatScore(summary.max_score),
        loss: formatScore(summary.average_loss),
      },
    )
  }
  const dominant = dominantLossRow.value
  if (dominant && dominant.averageLoss > 0) {
    return $t(
      '{scored} of {total} reports have a total score. The cohort average is {average}; {focus} has the highest average loss at {loss} points.',
      {
        scored: scoredReportScores.value.length,
        total: props.reportCount,
        average: formatScore(averageReportScore.value),
        focus: dominant.label,
        loss: formatScore(dominant.averageLoss),
      },
    )
  }
  return $t(
    '{scored} of {total} reports have a total score. The cohort average is {average}; no criterion loss is currently recorded.',
    {
      scored: scoredReportScores.value.length,
      total: props.reportCount,
      average: formatScore(averageReportScore.value),
    },
  )
})
const barWidth = (affectedCount: number, coveredCount: number) => {
  if (!affectedCount || !coveredCount) return '0%'
  return `${Math.max(3, Math.min(100, (affectedCount / coveredCount) * 100))}%`
}
const chartTitle = computed(() => {
  if (synthesisChartRows.value.length) return 'Affected reports'
  return props.selectedCriterionId ? 'Scoring point losses' : 'Criterion losses'
})
const metricItems = computed(() => {
  const summary = selectedSummary.value
  if (summary) {
    return [
      {
        label: $t('Average score'),
        shortLabel: $t('Avg'),
        value: `${formatScore(summary.average_score)}/${formatScore(summary.max_score)}`,
      },
      {
        label: $t('Average points lost'),
        shortLabel: $t('Loss'),
        value: formatScore(summary.average_loss),
      },
      {
        label: $t('Scored reports'),
        shortLabel: $t('Scored'),
        value: `${summary.statistical_count ?? summary.scored_count}/${summary.total_reports}`,
      },
    ]
  }
  return [
    {
      label: $t('Average score'),
      shortLabel: $t('Avg'),
      value: formatScore(averageReportScore.value),
    },
    {
      label: $t('Complete reports'),
      shortLabel: $t('Complete'),
      value: `${completeReportCount.value}/${props.reportCount}`,
    },
    {
      label: $t('Conflicts'),
      shortLabel: $t('Conflicts'),
      value: formatScore(totalConflictCount.value),
    },
  ]
})
</script>

<template>
  <section
    class="global-panel flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border shadow-sm"
    data-testid="cohort-summary-visualization"
    :aria-busy="synthesisLoading"
  >
    <div
      v-if="synthesisLoading"
      class="flex min-h-0 flex-1 flex-col"
      data-testid="cohort-summary-loading"
    >
      <div
        class="animate-pulse border-b border-[var(--border-subtle)] px-4 py-3 motion-reduce:animate-none"
        aria-hidden="true"
      >
        <span class="block h-2 w-24 rounded bg-slate-200"></span>
        <span class="mt-2 block h-4 w-44 rounded bg-slate-200"></span>
      </div>
      <div class="flex flex-1 flex-col items-center justify-center px-6 text-center" role="status">
        <span
          class="h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-[var(--interaction-active)] motion-reduce:animate-none"
          aria-hidden="true"
        ></span>
        <p class="mt-3 text-xs font-medium text-[var(--text-primary)]">
          {{ $t('Analyzing current cohort…') }}
        </p>
        <p class="mt-1 max-w-full truncate text-[10px] text-[var(--text-secondary)]">
          {{ selectedCriterionName }}
        </p>
      </div>
      <div
        class="grid grid-cols-2 gap-2 border-t border-[var(--border-subtle)] px-4 py-3"
        aria-hidden="true"
      >
        <span
          v-for="index in 4"
          :key="index"
          class="h-12 animate-pulse rounded-md bg-slate-100 motion-reduce:animate-none"
        ></span>
      </div>
    </div>
    <template v-else>
      <header
        class="shrink-0 border-b border-[var(--border-subtle)] px-4 py-2.5"
        data-testid="cohort-summary-header"
      >
        <span class="interaction-text block text-[10px] font-semibold uppercase tracking-[0.16em]">
          {{ $t(selectedCriterionId ? 'Criterion analysis' : 'Overall analysis') }}
        </span>
        <div class="mt-0.5 flex min-w-0 items-center justify-between gap-3">
          <h2 class="min-w-0 truncate text-base font-semibold text-[var(--text-primary)]">
            {{ selectedCriterionName }}
          </h2>
          <div class="flex shrink-0 items-center divide-x divide-[var(--border-subtle)]">
            <span
              v-for="metric in metricItems"
              :key="metric.label"
              class="inline-flex items-baseline gap-1 px-1.5 first:pl-0 last:pr-0"
              data-testid="cohort-summary-metric"
              :title="metric.label"
            >
              <span class="text-[8px] uppercase tracking-wide text-[var(--text-secondary)]">
                {{ metric.shortLabel }}
              </span>
              <strong class="text-[10px] font-semibold tabular-nums text-[var(--text-primary)]">
                {{ metric.value }}
              </strong>
            </span>
          </div>
        </div>
      </header>

      <div
        class="shrink-0 border-b border-[var(--border-subtle)] px-4 py-3"
        data-testid="cohort-analysis-text"
      >
        <p
          class="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-secondary)]"
        >
          {{ $t('Cohort synthesis') }}
        </p>
        <p
          class="mt-1.5 text-[11px] leading-[1.55] text-[var(--text-primary)]"
          data-testid="cohort-analysis"
        >
          {{ analysis || fallbackAnalysisText }}
        </p>
        <p v-if="synthesisError" class="mt-1.5 text-[10px] leading-4 text-amber-700">
          {{ $t('Model analysis unavailable. Loss data remains available.') }}
        </p>
      </div>

      <div class="flex min-h-0 flex-1 flex-col px-4 py-3" data-testid="cohort-analysis-chart">
        <p
          class="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-secondary)]"
        >
          {{ $t(chartTitle) }}
        </p>
        <div
          v-if="chartRows.length"
          class="mt-2 grid min-h-0 flex-1 snap-y snap-proximity grid-cols-2 gap-2 overflow-y-auto"
          data-testid="cohort-summary-loss-grid"
          :style="{ gridAutoRows: 'calc((100% - 16px) / 3)' }"
        >
          <button
            v-for="row in chartRows"
            :key="row.id"
            type="button"
            class="min-w-0 snap-start overflow-hidden rounded-md border border-[var(--border-subtle)] px-2.5 py-2 text-left transition-colors hover:border-[var(--interaction-active)] hover:bg-[var(--interaction-active-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--matrix-highlight)]"
            :class="activeFocusId === row.id ? 'interaction-soft interaction-outline' : 'bg-white'"
            data-testid="cohort-summary-loss-row"
            :data-focus-id="row.id"
            :aria-pressed="pinnedFocusId === row.id"
            @mouseenter="emit('highlight', row)"
            @mouseleave="emit('highlight', null)"
            @focus="emit('highlight', row)"
            @blur="emit('highlight', null)"
            @click="emit('toggleFocus', row)"
            @keydown.esc.stop="emit('clearFocus')"
          >
            <div class="flex items-start justify-between gap-3">
              <span
                class="min-w-0 truncate text-[11px] font-medium leading-4 text-[var(--text-primary)]"
                data-testid="cohort-summary-row-label"
                :title="row.label"
              >
                {{ row.label }}
              </span>
              <span
                class="shrink-0 text-[9px] tabular-nums text-[var(--text-secondary)]"
                data-testid="cohort-summary-row-meta"
              >
                {{ row.affectedCount }}/{{ row.coveredCount }} {{ $t('affected') }}
                <template v-if="row.averageLoss !== null">
                  · −{{ formatScore(row.averageLoss) }} {{ $t('avg.') }}
                </template>
                <template v-else-if="row.evidenceCount">
                  · {{ $t('{count} evidence refs', { count: row.evidenceCount }) }}
                </template>
              </span>
            </div>
            <div class="mt-1">
              <span class="block h-1.5 w-full overflow-hidden rounded-sm bg-slate-200">
                <span
                  class="block h-full rounded-sm bg-[var(--interaction-active)]"
                  :style="{ width: barWidth(row.affectedCount, row.coveredCount) }"
                  data-testid="cohort-summary-bar-fill"
                />
              </span>
            </div>
          </button>
        </div>
        <p v-else class="mt-3 text-xs text-slate-400">{{ $t('No loss data') }}</p>
      </div>

      <footer
        class="flex shrink-0 items-center justify-between border-t border-[var(--border-subtle)] px-4 py-2 text-[10px] text-[var(--text-secondary)]"
      >
        <span>{{ $t('Teacher grades') }} · {{ teacherCount }}</span>
        <span>{{ $t('Model grades') }} · {{ modelCount }}</span>
      </footer>
    </template>
  </section>
</template>
