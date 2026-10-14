import { createPinia, setActivePinia } from 'pinia'
import { vi } from 'vitest'
import { api } from '../api/client'
import { useGradingStore } from '../stores/grading'
import type { CriterionGlobalSummary, GlobalReportSummary, GlobalScorePoint } from '../types/schema'

export const criterion = (id: string, name: string, maxScore: number) => ({
  criteria_id: id,
  criteria_name: name,
  description: name,
  original_description: name,
  max_score: maxScore,
  section: 'analysis',
  few_shot_examples: [],
  scoring_points: [
    {
      point_id: `${id}.p1`,
      summary: `${name} point`,
      deduction_rules: [],
      max_deduction: maxScore,
    },
  ],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
})

export const point = (
  reportId: string,
  criterionId: string,
  score: number,
  maxScore: number,
  author: GlobalScorePoint['author'] = 'teacher',
): GlobalScorePoint => ({
  report_id: reportId,
  report_name: reportId,
  criterion_id: criterionId,
  event_id: `event-${criterionId}-${reportId}`,
  score,
  max_score: maxScore,
  author,
  conflict: false,
  included_in_statistics: true,
  anchor_text: 'selected evidence',
  evidence_anchors: [{ anchor_type: 'text', text: 'selected evidence', occurrence: 0 }],
  score_reason: 'Grounded reason',
  scoring_point_scores: [{ point_id: `${criterionId}.p1`, score, max_score: maxScore }],
})

export const summary = (
  criterionId: string,
  maxScore: number,
  averageLoss: number,
  points: GlobalScorePoint[],
): CriterionGlobalSummary => ({
  criteria_file_name: 'rubric.json',
  criterion_id: criterionId,
  max_score: maxScore,
  total_reports: 3,
  scored_count: points.length,
  statistical_count: points.length,
  ungraded_count: 3 - points.length,
  average_score: maxScore - averageLoss,
  average_loss: averageLoss,
  total_loss: averageLoss * points.length,
  teacher_count: points.filter((item) => item.author === 'teacher').length,
  model_count: points.filter((item) => item.author === 'model').length,
  conflict_count: 0,
  migration_conflict_count: 0,
  eligible_count: 0,
  eligible_report_names: [],
  retryable_count: 0,
  retryable_report_names: [],
  job_counts: {},
  point_loss_summaries: [
    {
      point_id: `${criterionId}.p1`,
      summary: `Point ${criterionId}`,
      average_loss: averageLoss,
      total_loss: averageLoss * points.length,
      covered_report_count: points.length,
      affected_report_count: points.filter((item) => item.score < maxScore).length,
      affected_report_ids: points
        .filter((item) => item.score < maxScore)
        .map((item) => item.report_id),
      refs: [],
    },
  ],
  points,
})

export const reports: GlobalReportSummary[] = [
  {
    report_id: 'report-a',
    report_name: 'report-a',
    analysis_summary:
      'The evidence supports the main claim, but the reasoning chain is incomplete. Accuracy requirements are fully met.',
    score: 94,
    base_score: 100,
    graded_criteria_count: 2,
    total_criteria_count: 2,
    conflict_count: 0,
    partial: false,
    provisional: false,
    teacher_count: 1,
    model_count: 1,
    criteria_scores: {
      '1': { status: 'graded', score: 6, max_score: 10, author: 'teacher' },
      '2': { status: 'graded', score: 5, max_score: 5, author: 'model' },
    },
  },
  {
    report_id: 'report-b',
    report_name: 'report-b',
    analysis_summary: 'The report is mostly complete, with limited support for one evidence claim.',
    score: 99,
    base_score: 100,
    graded_criteria_count: 1,
    total_criteria_count: 2,
    conflict_count: 0,
    partial: true,
    provisional: true,
    teacher_count: 0,
    model_count: 1,
    criteria_scores: {
      '1': { status: 'graded', score: 9, max_score: 10, author: 'model' },
      '2': { status: 'not_graded', score: null },
    },
  },
  {
    report_id: 'report-c',
    report_name: 'report-c',
    analysis_summary: '',
    score: null,
    base_score: 100,
    graded_criteria_count: 0,
    total_criteria_count: 2,
    conflict_count: 0,
    partial: true,
    provisional: true,
    teacher_count: 0,
    model_count: 0,
    criteria_scores: {
      '1': { status: 'not_graded', score: null },
      '2': { status: 'not_graded', score: null },
    },
  },
]

export const prepareGlobalViewStore = () => {
  const store = useGradingStore()
  store.setGlobalSelectedCriterion(null)
  store.activeCaseId = 'case-a'
  store.activeCriteriaFile = 'rubric.json'
  store.criteriaSchema = {
    schema_version: 'structured-rubric-v1',
    title: 'Rubric',
    background: '',
    language: 'zh',
    base_score: 100,
    criteria: [criterion('1', 'Evidence', 10), criterion('2', 'Accuracy', 5)],
  }
  store.reports = reports.map((item) => ({
    id: item.report_id,
    name: item.report_name,
    markdown: '# Report\n\nParagraph with selected evidence and context.',
    conversion_status: 'finished',
  }))
  const first = [point('report-a', '1', 6, 10), point('report-b', '1', 9, 10, 'model')]
  const second = [point('report-a', '2', 5, 5, 'model')]
  store.alignment = {
    readiness: [],
    open_conflicts: [],
    criterion_authorizations: [],
    criterion_global_summaries: [summary('1', 10, 2.5, first), summary('2', 5, 0, second)],
    global_report_summaries: reports,
    global_readiness: { allowed: true, blocker_count: 0, blockers: [] },
    global_projection_hash: 'projection-a',
    auto_grade_jobs: [],
    auto_grade_batches: [],
    audit_jobs: [],
    consistency_states: [],
    review_assignments: [],
    consistency_automation_mode: 'adaptive',
  }
  return store
}

export const prepareDenseGlobalViewStore = (reportCount = 14, criterionCount = 15) => {
  const store = useGradingStore()
  store.setGlobalSelectedCriterion(null)
  const denseCriteria = Array.from({ length: criterionCount }, (_, index) =>
    criterion(String(index + 1), `Criterion ${index + 1}`, 4),
  )
  const denseReports: GlobalReportSummary[] = Array.from(
    { length: reportCount },
    (_, reportIndex) => {
      const reportId = `report-${String(reportIndex + 1).padStart(2, '0')}`
      const criteriaScores = Object.fromEntries(
        denseCriteria.map((item, criterionIndex) => [
          item.criteria_id,
          {
            status: 'graded' as const,
            score: (reportIndex + criterionIndex) % 5,
            max_score: item.max_score,
            author: (reportIndex + criterionIndex) % 2 ? ('model' as const) : ('teacher' as const),
          },
        ]),
      )
      const score = Object.values(criteriaScores).reduce(
        (total, item) => total + Number(item.score),
        0,
      )
      const authorCounts = Object.values(criteriaScores).reduce(
        (counts, item) => ({
          teacher: counts.teacher + Number(item.author === 'teacher'),
          model: counts.model + Number(item.author === 'model'),
        }),
        { teacher: 0, model: 0 },
      )
      return {
        report_id: reportId,
        report_name: `Report ${reportIndex + 1}`,
        analysis_summary: `Analysis summary for Report ${reportIndex + 1}.`,
        score,
        base_score: criterionCount * 4,
        graded_criteria_count: criterionCount,
        total_criteria_count: criterionCount,
        conflict_count: 0,
        partial: false,
        provisional: false,
        teacher_count: authorCounts.teacher,
        model_count: authorCounts.model,
        criteria_scores: criteriaScores,
      }
    },
  )
  const denseSummaries = denseCriteria.map((item, criterionIndex) => {
    const points = denseReports.map((report, reportIndex) =>
      point(
        report.report_id,
        item.criteria_id,
        (reportIndex + criterionIndex) % 5,
        item.max_score,
        (reportIndex + criterionIndex) % 2 ? 'model' : 'teacher',
      ),
    )
    const averageScore =
      points.reduce((total, itemPoint) => total + itemPoint.score, 0) / points.length
    return {
      ...summary(item.criteria_id, item.max_score, item.max_score - averageScore, points),
      criteria_file_name: 'dense-rubric.json',
      total_reports: reportCount,
      ungraded_count: 0,
    }
  })

  store.activeCaseId = 'dense-case'
  store.activeCriteriaFile = 'dense-rubric.json'
  store.criteriaSchema = {
    schema_version: 'structured-rubric-v1',
    title: 'Dense rubric fixture',
    background: '',
    language: 'zh',
    base_score: criterionCount * 4,
    criteria: denseCriteria,
  }
  store.reports = denseReports.map((report) => ({
    id: report.report_id,
    name: report.report_name,
    markdown: '# Report\n\nSelected evidence for the dense fixture.',
    conversion_status: 'finished',
  }))
  store.alignment = {
    readiness: [],
    open_conflicts: [],
    criterion_authorizations: [],
    criterion_global_summaries: denseSummaries,
    global_report_summaries: denseReports,
    global_readiness: { allowed: true, blocker_count: 0, blockers: [] },
    global_projection_hash: 'dense-projection',
    auto_grade_jobs: [],
    auto_grade_batches: [],
    audit_jobs: [],
    consistency_states: [],
    review_assignments: [],
    consistency_automation_mode: 'adaptive',
  }
  return store
}

export const setupGlobalViewTest = () => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
  class ResizeObserverMock {
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.scrollTo = vi.fn()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  })
  vi.spyOn(api, 'getCohortSynthesis').mockImplementation(async (_caseId, payload) => ({
    status: 'ok',
    data: {
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      criterion_id: payload.criterion_id || null,
      projection_hash: 'projection-a',
      model_id: 'qa-model',
      language: 'en',
      analysis: payload.criterion_id
        ? 'Evidence deductions consistently reflect incomplete support and weak links between claims and cited material.'
        : 'Across the cohort, evidence quality is the clearest shared weakness, while accuracy losses are narrower and less severe.',
      insights: payload.criterion_id
        ? [
            {
              insight_id: 'evidence-chain',
              summary: 'Claims are not fully connected to supporting evidence',
              criterion_ids: ['1'],
              point_ids: ['1.p1'],
              report_ids: ['report-a', 'report-b'],
              supporting_refs: [
                { report_id: 'report-a', event_id: 'event-1-report-a' },
                { report_id: 'report-b', event_id: 'event-1-report-b' },
              ],
            },
          ]
        : [
            {
              insight_id: 'overall-evidence',
              summary: 'Evidence quality is the main cohort-wide weakness',
              criterion_ids: ['1'],
              point_ids: [],
              report_ids: ['report-a', 'report-b'],
              supporting_refs: [],
            },
            {
              insight_id: 'overall-accuracy',
              summary: 'Accuracy loss is isolated',
              criterion_ids: ['2'],
              point_ids: [],
              report_ids: ['report-a'],
              supporting_refs: [],
            },
          ],
      generated_at: '2026-08-28T00:00:00Z',
    },
  }))
}
