import type {
  CriteriaItem,
  CriterionGlobalSummary,
  GlobalReportSummary,
  GlobalScorePoint,
  ReportItem,
} from '../types/schema'

export type SortDirection = 'asc' | 'desc'
export const GLOBAL_ZOOM_MIN = 1
export const GLOBAL_ZOOM_MAX = 3

export interface HeatmapCriterionColumn {
  code: string
  criterionId: string
  summary: CriterionGlobalSummary
}

export interface RubricHeatmapGroup {
  key: string
  label: string
  maxScore: number
  columns: HeatmapCriterionColumn[]
}

export interface CategoryHistogramBin {
  count: number
  from: number
  to: number
  reportIds: string[]
}

export interface CategoryScoreHistogram {
  bins: CategoryHistogramBin[]
  completeCount: number
  excludedCount: number
  maxScore: number
  totalReports: number
}

export const normalizeGlobalZoom = (zoom: number) =>
  Math.min(Math.max(Math.round(zoom), GLOBAL_ZOOM_MIN), GLOBAL_ZOOM_MAX)

const compareNullableNumbers = (
  left: number | null | undefined,
  right: number | null | undefined,
  direction: SortDirection,
) => {
  const leftValid = Number.isFinite(left)
  const rightValid = Number.isFinite(right)
  if (!leftValid && !rightValid) return 0
  if (!leftValid) return 1
  if (!rightValid) return -1
  return direction === 'asc' ? Number(left) - Number(right) : Number(right) - Number(left)
}

export const sortCriterionSummaries = (
  summaries: CriterionGlobalSummary[],
  direction: SortDirection,
) =>
  [...summaries].sort(
    (left, right) =>
      compareNullableNumbers(left.average_loss, right.average_loss, direction) ||
      left.criterion_id.localeCompare(right.criterion_id, undefined, { numeric: true }),
  )

export const orderCriterionSummariesByRubric = (
  summaries: CriterionGlobalSummary[],
  criterionIds: Array<string | number>,
) => {
  const rubricPosition = new Map(
    criterionIds.map((criterionId, index) => [String(criterionId), index]),
  )
  return summaries
    .map((summary, sourceIndex) => ({ summary, sourceIndex }))
    .sort((left, right) => {
      const leftPosition = rubricPosition.get(String(left.summary.criterion_id))
      const rightPosition = rubricPosition.get(String(right.summary.criterion_id))
      if (leftPosition !== undefined && rightPosition !== undefined) {
        return leftPosition - rightPosition
      }
      if (leftPosition !== undefined) return -1
      if (rightPosition !== undefined) return 1
      return left.sourceIndex - right.sourceIndex
    })
    .map(({ summary }) => summary)
}

export const groupCriterionSummariesByCategory = (
  criteria: CriteriaItem[],
  summaries: CriterionGlobalSummary[],
  fallbackCategory: string,
): RubricHeatmapGroup[] => {
  const summaryById = new Map(summaries.map((summary) => [String(summary.criterion_id), summary]))
  const grouped = new Map<string, Omit<RubricHeatmapGroup, 'key'>>()
  const includedCriterionIds = new Set<string>()

  const appendColumn = (
    label: string,
    summary: CriterionGlobalSummary,
    rubricIndex: number,
    maxScore: number,
  ) => {
    const group = grouped.get(label) || { label, maxScore: 0, columns: [] }
    group.columns.push({
      code: `R${rubricIndex + 1}`,
      criterionId: String(summary.criterion_id),
      summary,
    })
    group.maxScore += Number.isFinite(maxScore) ? maxScore : 0
    grouped.set(label, group)
    includedCriterionIds.add(String(summary.criterion_id))
  }

  criteria.forEach((criterion, rubricIndex) => {
    const criterionId = String(criterion.criteria_id)
    const summary = summaryById.get(criterionId)
    if (!summary) return
    appendColumn(
      criterion.section?.trim() || fallbackCategory,
      summary,
      rubricIndex,
      Number(criterion.max_score),
    )
  })

  summaries.forEach((summary, sourceIndex) => {
    if (includedCriterionIds.has(String(summary.criterion_id))) return
    appendColumn(
      fallbackCategory,
      summary,
      criteria.length + sourceIndex,
      Number(summary.max_score),
    )
  })

  return Array.from(grouped.values()).map((group, index) => ({
    ...group,
    key: `category-${index + 1}`,
  }))
}

export const buildCategoryScoreHistogram = (
  group: RubricHeatmapGroup,
  reports: GlobalReportSummary[],
  binCount = 8,
): CategoryScoreHistogram => {
  const safeBinCount = Math.max(1, Math.round(binCount))
  const maxScore = Math.max(0, Number(group.maxScore) || 0)
  const completeCategoryScore = (report: GlobalReportSummary) => {
    let total = 0
    for (const column of group.columns) {
      const scoreState = report.criteria_scores[column.criterionId]
      if (scoreState?.score === null || scoreState?.score === undefined) return null
      const score = Number(scoreState?.score)
      if (scoreState?.status !== 'graded' || !Number.isFinite(score)) return null
      total += score
    }
    return { reportId: report.report_id, score: total }
  }
  const totals = reports
    .map(completeCategoryScore)
    .filter((score): score is { reportId: string; score: number } => score !== null)
  const bins = Array.from({ length: safeBinCount }, (_, index) => ({
    count: 0,
    from: maxScore > 0 ? (maxScore * index) / safeBinCount : 0,
    to: maxScore > 0 ? (maxScore * (index + 1)) / safeBinCount : 0,
    reportIds: [] as string[],
  }))

  totals.forEach(({ reportId, score }) => {
    const ratio = maxScore > 0 ? Math.max(0, Math.min(1, score / maxScore)) : 0
    const index = Math.min(safeBinCount - 1, Math.floor(ratio * safeBinCount))
    bins[index]!.count += 1
    bins[index]!.reportIds.push(reportId)
  })

  return {
    bins,
    completeCount: totals.length,
    excludedCount: reports.length - totals.length,
    maxScore,
    totalReports: reports.length,
  }
}

export const sortReportSummaries = (
  reports: GlobalReportSummary[],
  direction: SortDirection,
  criterionId: string | null,
) =>
  [...reports].sort((left, right) => {
    const leftScore = criterionId ? left.criteria_scores[criterionId]?.score : left.score
    const rightScore = criterionId ? right.criteria_scores[criterionId]?.score : right.score
    return (
      compareNullableNumbers(leftScore, rightScore, direction) ||
      left.report_name.localeCompare(right.report_name, undefined, { numeric: true })
    )
  })

export const autoZoomForReportCount = (reportCount: number) => {
  if (reportCount <= 1) return 3
  if (reportCount <= 5) return 2
  return 1
}

export const tileMinimumForZoom = (zoom: number) => {
  const safe = normalizeGlobalZoom(zoom)
  return [0, 240, 320, 520][safe]!
}

export const columnCountForOverview = (
  width: number,
  reportCount: number,
  zoom: number,
  gap = 12,
) => {
  if (reportCount <= 0 || width <= 0) return 1
  if (normalizeGlobalZoom(zoom) >= GLOBAL_ZOOM_MAX) return 1
  const minimum = tileMinimumForZoom(zoom)
  return Math.max(1, Math.min(reportCount, Math.floor((width + gap) / (minimum + gap))))
}

const normalizedIndex = (markdown: string, text: string) => {
  if (!markdown || !text.trim()) return null
  const direct = markdown.toLocaleLowerCase().indexOf(text.trim().toLocaleLowerCase())
  if (direct >= 0) return direct
  const compactMarkdown = markdown.replace(/\s+/g, ' ').toLocaleLowerCase()
  const compactText = text.trim().replace(/\s+/g, ' ').toLocaleLowerCase()
  const compact = compactMarkdown.indexOf(compactText)
  return compact >= 0 ? compact : null
}

export const markerPosition = (
  report: Pick<ReportItem, 'markdown'> | null,
  point: GlobalScorePoint,
) => {
  const markdown = report?.markdown || ''
  const anchors = point.evidence_anchors?.length
    ? point.evidence_anchors
    : [{ text: point.anchor_text || '' }]
  const positions = anchors
    .map((anchor) => {
      if (anchor.anchor_type === 'absence') {
        const expected = String(anchor.expected_location || '')
        if (expected.toLocaleLowerCase() === 'document:start') return 0
        if (expected.toLocaleLowerCase() === 'document:end') return markdown.length
        return normalizedIndex(markdown, expected)
      }
      return normalizedIndex(
        markdown,
        String(anchor.text || anchor.image_alt || anchor.image_src || ''),
      )
    })
    .filter((value): value is number => value !== null)
  if (!positions.length || !markdown.length) return null
  return Math.min(Math.max((Math.min(...positions) / markdown.length) * 100, 2), 98)
}

export const globalPointMap = (summaries: CriterionGlobalSummary[]) => {
  const map = new Map<string, GlobalScorePoint>()
  summaries.forEach((summary) =>
    summary.points.forEach((point) =>
      map.set(`${point.report_id}::${summary.criterion_id}`, point),
    ),
  )
  return map
}
