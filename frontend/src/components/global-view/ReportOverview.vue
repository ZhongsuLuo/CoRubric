<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useGradingStore } from '../../stores/grading'
import type { CriterionGlobalSummary, GlobalReportSummary } from '../../types/schema'
import {
  autoZoomForReportCount,
  globalPointMap,
  normalizeGlobalZoom,
  sortReportSummaries,
} from '../../utils/globalView'
import ReportTile from './ReportTile.vue'
import { translate as $t } from '../../i18n'
import type { ReportTone } from './presentation'

const props = defineProps<{
  summaries: CriterionGlobalSummary[]
  reportSummaries: GlobalReportSummary[]
  criterionMaxScores: Record<string, number>
  selectedCriterionId: string | null
  highlightedReportIds: string[]
  highlightedCriterionIds: string[]
}>()

const store = useGradingStore()
const overviewRef = ref<HTMLElement | null>(null)

const pointsByReportCriterion = computed(() => globalPointMap(props.summaries))
const highlightedReportIdSet = computed(() => new Set(props.highlightedReportIds))
const pointFor = (reportId: string, criterionId: string | null) =>
  criterionId ? pointsByReportCriterion.value.get(`${reportId}::${criterionId}`) || null : null
const markdownFor = (reportId: string) =>
  store.reports.find((report) => report.id === reportId)?.markdown || ''
const sortedReports = computed(() =>
  sortReportSummaries(
    props.reportSummaries,
    store.globalReportSortDirection,
    props.selectedCriterionId,
  ),
)
const currentZoom = computed(() =>
  normalizeGlobalZoom(
    store.globalZoomByCase[store.activeCaseId] ??
      autoZoomForReportCount(props.reportSummaries.length),
  ),
)

const reportScore = (report: GlobalReportSummary) =>
  props.selectedCriterionId
    ? report.criteria_scores[props.selectedCriterionId]?.score
    : report.score
const reportMaxScore = (report: GlobalReportSummary) =>
  props.selectedCriterionId
    ? props.criterionMaxScores[props.selectedCriterionId]
    : report.base_score
const reportStatus = (report: GlobalReportSummary) => {
  if (!props.selectedCriterionId) {
    if (report.conflict_count) return $t('Conflict')
    if (!report.graded_criteria_count) {
      const statuses = new Set(
        store.alignment.auto_grade_jobs
          .filter((job) => job.report_name === report.report_id)
          .map((job) => job.status),
      )
      const labels = [
        statuses.has('running') && $t('Model grading · In progress'),
        statuses.has('pending') && $t('Model grading · Queued'),
        statuses.has('failed') && $t('Auto-grading failed'),
        statuses.has('abstained') && $t('Cannot auto-grade · Teacher grading needed'),
      ].filter((value): value is string => Boolean(value))
      return labels.length ? labels.join(' · ') : $t('Not graded')
    }
    return report.partial ? $t('Grading incomplete') : $t('Complete')
  }
  const point = pointFor(report.report_id, props.selectedCriterionId)
  if (point?.conflict) return $t('Conflict')
  if (point?.validity_status === 'model_regrade_required') return $t('Auto-grading failed')
  if (point?.validity_status === 'teacher_repair_required') {
    return $t('Teacher confirmation needed')
  }
  if (point) return $t('Graded')
  const job = store.getCriterionAutoGradeJob(report.report_id, props.selectedCriterionId)
  if (job?.status === 'pending') return $t('Model grading · Queued')
  if (job?.status === 'running') return $t('Model grading · In progress')
  if (job?.status === 'failed') return $t('Auto-grading failed')
  if (job?.status === 'abstained') return $t('Cannot auto-grade · Teacher grading needed')
  return $t('Not graded')
}
const reportTone = (report: GlobalReportSummary): ReportTone => {
  if (!props.selectedCriterionId) {
    if (report.conflict_count) return 'conflict'
    const statuses = new Set(
      store.alignment.auto_grade_jobs
        .filter((job) => job.report_name === report.report_id)
        .map((job) => job.status),
    )
    if (statuses.has('failed')) return 'conflict'
    if (statuses.has('running')) return 'running'
    if (statuses.has('abstained') || report.partial) return 'review'
    return report.graded_criteria_count ? 'verified' : 'neutral'
  }
  const point = pointFor(report.report_id, props.selectedCriterionId)
  if (point?.conflict || point?.validity_status === 'model_regrade_required') return 'conflict'
  if (point?.validity_status === 'teacher_repair_required') return 'review'
  if (point) return 'verified'
  const job = store.getCriterionAutoGradeJob(report.report_id, props.selectedCriterionId)
  if (job?.status === 'failed') return 'conflict'
  if (job?.status === 'running') return 'running'
  if (job?.status === 'abstained') return 'review'
  return 'neutral'
}
const provenanceLabel = (report: GlobalReportSummary) => {
  if (props.selectedCriterionId) {
    const point = pointFor(report.report_id, props.selectedCriterionId)
    if (!point) return ''
    if (point.acceptance_status === 'manual_exception') return $t('Teacher exception')
    return point.author === 'model' ? $t('Model') : point.author === 'teacher' ? $t('Teacher') : ''
  }
  if (report.teacher_count && report.model_count) return $t('Teacher + Model')
  if (report.model_count) return $t('Model')
  if (report.teacher_count) return $t('Teacher')
  return ''
}
const inspectionPoint = (report: GlobalReportSummary) => {
  if (props.selectedCriterionId) return pointFor(report.report_id, props.selectedCriterionId)
  for (const criterionId of props.highlightedCriterionIds) {
    const point = pointFor(report.report_id, criterionId)
    if (point) return point
  }
  return null
}
const inspectReport = (report: GlobalReportSummary) => {
  const point = inspectionPoint(report)
  store.inspectInReportView(
    report.report_id,
    props.selectedCriterionId || point?.criterion_id || null,
    point?.event_id || null,
  )
}
watch(
  () => [store.activeCaseId, props.reportSummaries.length],
  () => {
    store.setGlobalFocusedReport(null)
  },
)
watch(
  () => [props.selectedCriterionId, store.globalFocusedReportId],
  async () => {
    if (!store.globalFocusedReportId) return
    await nextTick()
    const target = Array.from(
      overviewRef.value?.querySelectorAll<HTMLElement>('[data-report-cell]') || [],
    ).find((element) => element.dataset.reportCell === store.globalFocusedReportId)
    const scroller = overviewRef.value
    if (!target || !scroller) return
    const targetTop =
      target.offsetTop - Math.max((scroller.clientHeight - target.offsetHeight) / 2, 0)
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    scroller.scrollTo({
      top: Math.max(targetTop, 0),
      behavior: reducedMotion ? 'auto' : 'smooth',
    })
  },
  { immediate: true },
)
</script>

<template>
  <main class="flex min-h-0 min-w-0 flex-col bg-white">
    <div
      v-if="selectedCriterionId"
      class="grid shrink-0 grid-cols-[minmax(150px,0.7fr)_82px_minmax(210px,1.25fr)_minmax(230px,1.45fr)_112px] items-center gap-4 border-b border-slate-200 bg-slate-50/70 px-5 py-2 text-[9px] font-semibold uppercase tracking-[0.14em] text-slate-400"
      data-testid="report-table-header"
    >
      <span>{{ $t('Report') }}</span>
      <span class="text-right">{{ $t('Score') }}</span>
      <span>{{ $t('Highlighted text') }}</span>
      <span>{{ $t('Scoring rationale') }}</span>
      <span class="text-right">{{ $t('Actions') }}</span>
    </div>
    <div
      v-else
      class="grid shrink-0 grid-cols-[minmax(150px,220px)_76px_minmax(260px,1fr)_minmax(150px,0.65fr)_90px] items-center gap-4 border-b border-slate-200 bg-slate-50/70 px-5 py-2 text-[9px] font-semibold uppercase tracking-[0.14em] text-slate-400"
      data-testid="report-table-header"
    >
      <span>{{ $t('Report') }}</span>
      <span class="text-right">{{ $t('Total score') }}</span>
      <span>{{ $t('Summary') }}</span>
      <span>{{ $t('Status') }}</span>
      <span class="text-right">{{ $t('Actions') }}</span>
    </div>

    <div
      ref="overviewRef"
      class="scrollbar-hidden min-h-0 flex-1 overflow-y-auto outline-none"
      :aria-label="$t('Report overview rows')"
      data-testid="global-report-overview"
      tabindex="0"
    >
      <div
        v-if="!sortedReports.length"
        class="flex min-h-full items-center justify-center text-sm text-slate-500"
      >
        {{ $t('No reports in this case.') }}
      </div>
      <div v-else class="divide-y divide-slate-200">
        <div
          v-for="report in sortedReports"
          :key="report.report_id"
          class="min-w-0 bg-white transition-colors hover:bg-slate-50/50"
          :data-report-cell="report.report_id"
        >
          <ReportTile
            :report="report"
            :point="pointFor(report.report_id, selectedCriterionId)"
            :selected-criterion-id="selectedCriterionId"
            :highlighted="highlightedReportIdSet.has(report.report_id)"
            :status="reportStatus(report)"
            :tone="reportTone(report)"
            :score="reportScore(report)"
            :max-score="reportMaxScore(report)"
            :provenance="provenanceLabel(report)"
            :focused="store.globalFocusedReportId === report.report_id"
            :density="currentZoom"
            :markdown="markdownFor(report.report_id)"
            @inspect="inspectReport(report)"
          />
        </div>
      </div>
    </div>
  </main>
</template>
