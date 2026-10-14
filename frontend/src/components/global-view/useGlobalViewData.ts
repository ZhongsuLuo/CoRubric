import { computed } from 'vue'
import { useGradingStore } from '../../stores/grading'
import type { GlobalReportSummary } from '../../types/schema'
import {
  groupCriterionSummariesByCategory,
  orderCriterionSummariesByRubric,
  sortReportSummaries,
} from '../../utils/globalView'
import { translate as t } from '../../i18n'

export const useGlobalViewData = () => {
  const store = useGradingStore()

  const criteria = computed(() => store.criteriaSchema?.criteria || [])
  const criteriaById = computed(
    () => new Map(criteria.value.map((item) => [String(item.criteria_id), item])),
  )
  const criterionNames = computed(() =>
    Object.fromEntries(
      criteria.value.map((item) => [String(item.criteria_id), item.criteria_name]),
    ),
  )
  const criterionMaxScores = computed(() =>
    Object.fromEntries(
      criteria.value.map((item) => [String(item.criteria_id), Number(item.max_score)]),
    ),
  )
  const summaries = computed(() =>
    store.alignment.criterion_global_summaries.filter((item) =>
      store.belongsToActiveCriteriaFile(item.criteria_file_name),
    ),
  )
  const summariesById = computed(
    () => new Map(summaries.value.map((item) => [String(item.criterion_id), item])),
  )
  const rubricOrderedSummaries = computed(() =>
    orderCriterionSummariesByRubric(
      summaries.value,
      criteria.value.map((item) => item.criteria_id),
    ),
  )
  const heatmapGroups = computed(() =>
    groupCriterionSummariesByCategory(
      criteria.value,
      rubricOrderedSummaries.value,
      t('Category pending update'),
    ),
  )
  const selectedCriterionId = computed(() => store.globalSelectedCriterionId)
  const selectedCriterionName = computed(
    () =>
      (selectedCriterionId.value &&
        criteriaById.value.get(selectedCriterionId.value)?.criteria_name) ||
      t('Overall'),
  )

  const fallbackReports = computed<GlobalReportSummary[]>(() =>
    store.reports.map((report) => ({
      report_id: report.id,
      report_name: report.name,
      analysis_summary: '',
      score: null,
      base_score: store.criteriaSchema?.base_score || 100,
      graded_criteria_count: 0,
      total_criteria_count: criteria.value.length,
      conflict_count: 0,
      partial: true,
      provisional: true,
      teacher_count: 0,
      model_count: 0,
      criteria_scores: {},
    })),
  )
  const reportSummaries = computed(
    () => store.alignment.global_report_summaries || fallbackReports.value,
  )
  const overallReports = computed(() =>
    sortReportSummaries(reportSummaries.value, store.globalReportSortDirection, null),
  )

  return {
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
  }
}
