import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { setLocale } from '../i18n'
import { api } from '../api/client'
import {
  autoZoomForReportCount,
  buildCategoryScoreHistogram,
  columnCountForOverview,
  groupCriterionSummariesByCategory,
  orderCriterionSummariesByRubric,
  sortCriterionSummaries,
  sortReportSummaries,
} from '../utils/globalView'
import GlobalView from './global-view/GlobalView.vue'
import type { ApiResponse, AutoGradeBatch, CohortSynthesis } from '../types/schema'
import {
  prepareDenseGlobalViewStore,
  prepareGlobalViewStore,
  criterion,
  reports,
  setupGlobalViewTest,
  summary,
} from './globalViewTestFixture'

beforeEach(() => {
  setLocale('en')
  setupGlobalViewTest()
})

describe('Global view business logic', () => {
  const batch = (status: AutoGradeBatch['status']): AutoGradeBatch => ({
    batch_id: 'batch-1',
    case_id: 'case-a',
    criteria_file_name: 'rubric.json',
    rubric_version: 'rubric-v1',
    model_id: 'model-a',
    status,
    created_at: '2026-08-13T00:00:00Z',
    updated_at: '2026-08-13T00:00:00Z',
    report_names: ['report-a'],
    criteria: [{ criterion_id: '1', criterion_name: 'Evidence', max_score: 10 }],
    targets: [{ report_name: 'report-a', criterion_id: '1', status: 'pending' }],
    progress: {
      total: 1,
      completed: 0,
      percent: 0,
      pending: 1,
      running: 0,
      done: 0,
      abstained: 0,
      failed: 0,
      stale: 0,
    },
    calibration: {
      required: status === 'awaiting_calibration',
      status: status === 'awaiting_calibration' ? 'pending' : 'confirmed',
      rubric_version: 'rubric-v1',
    },
  })

  it('uses deterministic loss/report sorting and dynamic scale sizes', () => {
    const criterionItems = [summary('1', 10, 3, []), summary('2', 5, 1, [])]
    expect(sortCriterionSummaries(criterionItems, 'desc').map((item) => item.criterion_id)).toEqual(
      ['1', '2'],
    )
    expect(
      orderCriterionSummariesByRubric(criterionItems, ['2', '1']).map((item) => item.criterion_id),
    ).toEqual(['2', '1'])
    expect(sortReportSummaries(reports, 'asc', null).map((item) => item.report_id)).toEqual([
      'report-a',
      'report-b',
      'report-c',
    ])
    expect([0, 1, 5, 15, 20, 50, 51].map(autoZoomForReportCount)).toEqual([3, 3, 2, 1, 1, 1, 1])
    expect(columnCountForOverview(1200, 15, 1)).toBeGreaterThan(columnCountForOverview(1200, 15, 2))
    expect(columnCountForOverview(1200, 50, 3)).toBe(1)
  })

  it('groups heatmap criteria by category while preserving original R numbers', () => {
    const criteria = [
      { ...criterion('1', 'Evidence', 10), section: 'Information completeness' },
      { ...criterion('2', 'Accuracy', 5), section: 'Calculation quality' },
      { ...criterion('3', 'Traceability', 4), section: 'Information completeness' },
    ]
    const groups = groupCriterionSummariesByCategory(
      criteria,
      [summary('1', 10, 3, []), summary('2', 5, 1, []), summary('3', 4, 2, [])],
      'Category pending update',
    )

    expect(groups.map((group) => group.label)).toEqual([
      'Information completeness',
      'Calculation quality',
    ])
    expect(groups[0]!.columns.map((column) => [column.criterionId, column.code])).toEqual([
      ['1', 'R1'],
      ['3', 'R3'],
    ])
    expect(groups[1]!.columns.map((column) => [column.criterionId, column.code])).toEqual([
      ['2', 'R2'],
    ])

    const histogram = buildCategoryScoreHistogram(groups[0]!, [
      {
        ...reports[0]!,
        criteria_scores: {
          '1': { status: 'graded', score: 6, max_score: 10 },
          '3': { status: 'graded', score: 2, max_score: 4 },
        },
      },
      {
        ...reports[1]!,
        criteria_scores: {
          '1': { status: 'graded', score: 9, max_score: 10 },
          '3': { status: 'not_graded', score: null },
        },
      },
      {
        ...reports[2]!,
        criteria_scores: {
          '1': { status: 'graded', score: 8, max_score: 10 },
          '3': { status: 'conflict' },
        },
      },
    ])

    expect(histogram.maxScore).toBe(14)
    expect(histogram.completeCount).toBe(1)
    expect(histogram.excludedCount).toBe(2)
    expect(histogram.bins.reduce((total, bin) => total + bin.count, 0)).toBe(1)
    expect(histogram.bins.find((bin) => bin.count)?.reportIds).toEqual(['report-a'])
  })

  it('keeps the complete 14 by 15 cohort visible when a criterion is selected', async () => {
    const store = prepareDenseGlobalViewStore()
    const wrapper = mount(GlobalView)

    const expectCompleteCohort = () => {
      expect(wrapper.findAll('[data-testid^="heatmap-criterion-"]')).toHaveLength(15)
      expect(wrapper.findAll('[data-testid^="heatmap-report-label-"]')).toHaveLength(14)
      expect(wrapper.findAll('[data-testid^="heatmap-cell-"]')).toHaveLength(210)
      expect(wrapper.findAll('[data-testid="global-report-tile"]')).toHaveLength(14)
      expect(wrapper.findAll('[data-testid^="heatmap-score-dot-"]')).toHaveLength(0)
    }

    expectCompleteCohort()
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-1"]').classes()).toContain(
      'heatmap-cell-loss-severe',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-2"]').classes()).toContain(
      'heatmap-cell-loss-high',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-3"]').classes()).toContain(
      'heatmap-cell-loss-medium',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-4"]').classes()).toContain(
      'heatmap-cell-loss-low',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-5"]').classes()).toContain(
      'heatmap-cell-loss-none',
    )
    await wrapper.get('[data-testid="heatmap-criterion-7"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(store.globalSelectedCriterionId).toBe('7')
    expectCompleteCohort()
    wrapper.unmount()
  })

  it('renders a complete 50 by 20 cohort without truncating the matrix or report list', async () => {
    const store = prepareDenseGlobalViewStore(50, 20)
    const wrapper = mount(GlobalView)

    expect(wrapper.findAll('[data-testid^="heatmap-criterion-"]')).toHaveLength(20)
    expect(wrapper.findAll('[data-testid^="heatmap-report-label-"]')).toHaveLength(50)
    expect(wrapper.findAll('[data-testid^="heatmap-cell-"]')).toHaveLength(1000)
    expect(wrapper.findAll('[data-testid="global-report-tile"]')).toHaveLength(50)
    expect(wrapper.findAll('[data-testid^="heatmap-score-dot-"]')).toHaveLength(0)

    await wrapper.get('[data-testid="heatmap-criterion-20"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(store.globalSelectedCriterionId).toBe('20')
    expect(wrapper.findAll('[data-testid^="heatmap-cell-"]')).toHaveLength(1000)
    expect(wrapper.findAll('[data-testid="global-report-tile"]')).toHaveLength(50)
    wrapper.unmount()
  })

  it('uses a patterned conflict state instead of a quantitative loss fill', () => {
    const store = prepareGlobalViewStore()
    store.alignment.global_report_summaries = (store.alignment.global_report_summaries || []).map(
      (report) => ({
        ...report,
        criteria_scores: { ...report.criteria_scores },
      }),
    )
    store.alignment.global_report_summaries![0]!.criteria_scores['1'] = {
      status: 'conflict',
      score: 6,
      max_score: 10,
      author: 'teacher',
    }
    const wrapper = mount(GlobalView)
    const cell = wrapper.get('[data-testid="heatmap-cell-report-a-1"]')

    expect(cell.classes()).toContain('heatmap-cell-conflict')
    expect(cell.classes().some((name) => name.startsWith('heatmap-cell-loss-'))).toBe(false)
    expect(cell.attributes('aria-label')).toContain('conflict')
    expect(wrapper.find('[data-testid^="heatmap-score-dots-"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows criterion evidence and scoring rationale instead of the full report preview', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const longRationale = 'Grounded scoring rationale. '.repeat(20).trim()
    store.alignment.criterion_global_summaries[0]!.points[0]!.score_reason = longRationale
    const completeSuffix = 'FINAL CONTENT MUST REMAIN VISIBLE'
    store.reports[0]!.markdown = `# Report\n\n${'Long report content. '.repeat(40)}${completeSuffix}`
    const wrapper = mount(GlobalView)
    const row = wrapper.get('[data-report-id="report-a"]')
    const evidence = row.get('[data-testid="global-evidence-text"]')
    const rationale = row.get('[data-testid="global-score-rationale"]')

    expect(evidence.text()).toContain('selected evidence')
    expect(rationale.text()).toBe(longRationale)
    expect(rationale.classes()).not.toContain('overflow-y-auto')
    expect(rationale.classes().some((name) => name.startsWith('max-h-'))).toBe(false)
    expect(row.text()).not.toContain(completeSuffix)
    expect(wrapper.text()).toContain('Highlighted text')
    expect(wrapper.text()).toContain('Scoring rationale')
    expect(wrapper.text()).not.toContain('Document signal')
    expect(wrapper.text()).not.toContain('Map')
    const overview = wrapper.get('[data-testid="global-report-overview"]')
    expect(overview.classes()).toContain('scrollbar-hidden')
    expect(overview.attributes('tabindex')).toBe('0')
  })

  it('renders every report highlight anchor as a separate visible evidence snippet', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const reportPoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    reportPoint.evidence_anchors = [
      {
        anchor_type: 'text',
        text: 'First highlighted sentence',
        occurrence: 0,
      },
      {
        anchor_type: 'text',
        text: 'Second highlighted sentence',
        occurrence: 0,
      },
    ]
    const wrapper = mount(GlobalView)
    const row = wrapper.get('[data-report-id="report-a"]')
    const snippets = row.findAll('[data-testid="global-highlighted-snippet"]')

    expect(snippets).toHaveLength(2)
    expect(snippets.map((snippet) => snippet.text())).toEqual([
      '1First highlighted sentence',
      '2Second highlighted sentence',
    ])
    expect(row.get('[data-testid="global-evidence-text"]').classes()).not.toContain(
      'overflow-y-auto',
    )
  })

  it('renders image evidence as a modal preview without leaving global view', async () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    store.toggleMode('global')
    const reportPoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    reportPoint.event_id = 'evt-images'
    reportPoint.anchor_text = ''
    reportPoint.evidence_anchors = [
      { anchor_type: 'text', text: 'selected evidence', occurrence: 0 },
      {
        anchor_type: 'image',
        image_src: 'images/chart.png',
        description: 'A very long chart description that should not be shown in the overview.',
      },
    ]
    store.reports[0]!.markdown =
      'selected evidence\n\n![Chart](/case-files/case-a/markdown/report-a/images/chart.png)'
    store.hydrateHighlights('report-a', {
      file_name: 'report-a.md',
      language: 'en',
      base_score: 100,
      file_score: 96,
      criteria: [
        {
          ...store.criteriaSchema!.criteria[0]!,
          section: 'analysis',
          criteria_score: 6,
          file_original_content: 'selected evidence',
          target_section: 'analysis',
          score_reason: 'Grounded reason',
          anchor_text: 'selected evidence',
          evidence_anchors: reportPoint.evidence_anchors,
          graded_by: 'model',
          provenance: { event_id: 'evt-images', grader_type: 'model' },
        },
      ],
    })
    const wrapper = mount(GlobalView, { attachTo: document.body })
    const row = wrapper.get('[data-report-id="report-a"]')

    expect(row.findAll('[data-testid="global-highlighted-snippet"]')).toHaveLength(1)
    expect(row.text()).not.toContain('A very long chart description')
    const imageLink = row.get('[data-testid="global-image-evidence-link"]')
    expect(imageLink.text()).toContain('Image evidence 1')

    await imageLink.trigger('click')
    const preview = document.body.querySelector('[data-testid="image-evidence-preview"]')
    const image = document.body.querySelector<HTMLImageElement>(
      '[data-testid="image-evidence-preview-image"]',
    )
    expect(preview).not.toBeNull()
    expect(image?.getAttribute('src')).toBe(
      '/api/case-files/case-a/markdown/report-a/images/chart.png',
    )
    expect(store.mode).toBe('global')

    document.body
      .querySelector<HTMLButtonElement>('[data-testid="close-image-evidence-preview"]')!
      .click()
    await wrapper.vm.$nextTick()
    expect(document.body.querySelector('[data-testid="image-evidence-preview"]')).toBeNull()
    wrapper.unmount()
  })

  it('turns a legacy text-only image block into an image link', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const reportPoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    reportPoint.evidence_anchors = []
    reportPoint.anchor_text =
      '> **[Image]** Legacy long visual description.\n> *Source: images/legacy.png*'
    store.reports[0]!.markdown = '![Legacy](/case-files/case-a/markdown/report-a/images/legacy.png)'
    const wrapper = mount(GlobalView, { attachTo: document.body })
    const row = wrapper.get('[data-report-id="report-a"]')

    expect(row.find('[data-testid="global-image-evidence-link"]').exists()).toBe(true)
    expect(row.text()).not.toContain('Legacy long visual description')
    wrapper.unmount()
  })

  it('separates absence evidence from text that is highlighted in the report', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const reportPoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    reportPoint.anchor_text = 'Nearby but unrelated instrument description.'
    reportPoint.evidence_anchors = [
      {
        anchor_type: 'absence',
        missing_item: 'limitations section',
        expected_location: 'document:end',
      },
    ]
    const wrapper = mount(GlobalView)
    const row = wrapper.get('[data-report-id="report-a"]')

    expect(row.findAll('[data-testid="global-highlighted-snippet"]')).toHaveLength(0)
    expect(row.text()).not.toContain('Nearby but unrelated instrument description.')
    expect(row.get('[data-testid="global-no-matching-evidence"]').text()).toContain(
      'No matching evidence',
    )
    expect(row.get('[data-testid="global-missing-evidence"]').text()).toContain(
      'limitations section',
    )
  })

  it('shows both partial image evidence and the remaining missing requirement', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const reportPoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    reportPoint.evidence_anchors = [
      {
        anchor_type: 'image',
        image_src: 'images/atomizer.png',
        description: 'Atomizer only',
      },
      {
        anchor_type: 'absence',
        expected_location: 'document:end',
        missing_item: 'complete instrument diagram',
      },
    ]
    store.reports[0]!.markdown =
      '![Atomizer](/case-files/case-a/markdown/report-a/images/atomizer.png)'
    const wrapper = mount(GlobalView)
    const row = wrapper.get('[data-report-id="report-a"]')

    expect(row.find('[data-testid="global-image-evidence-link"]').exists()).toBe(true)
    expect(row.get('[data-testid="global-missing-evidence"]').text()).toContain(
      'complete instrument diagram',
    )
  })

  it('shows a criterion-by-report analysis matrix and links a cell to report evidence', async () => {
    const store = prepareGlobalViewStore()
    const wrapper = mount(GlobalView, { attachTo: document.body })

    const heatmap = wrapper.get('[data-testid="global-rubric-heatmap"]')
    expect(wrapper.get('[data-testid="heatmap-code-1"]').text()).toBe('R1')
    expect(wrapper.get('[data-testid="heatmap-name-1"]').text()).toBe('Evidence')
    expect(wrapper.get('[data-testid="heatmap-tooltip-1"]').text()).toContain(
      'Average score 7.5/10',
    )
    expect(wrapper.get('[data-testid="heatmap-tooltip-1"]').classes()).toContain(
      'group-hover:block',
    )
    expect(wrapper.get('[data-testid="heatmap-report-header"]').attributes('style')).toContain(
      'grid-column: 1',
    )
    expect(wrapper.get('[data-testid="heatmap-report-header"]').attributes('style')).toContain(
      'grid-row: 3',
    )
    expect(wrapper.get('[data-testid="heatmap-report-header"]').classes()).toContain('top-[74px]')
    expect(wrapper.get('[data-testid="heatmap-criterion-1"]').attributes('style')).toContain(
      'grid-column: 2',
    )
    expect(
      wrapper.get('[data-testid="heatmap-report-label-report-a"]').attributes('style'),
    ).toContain('grid-column: 1')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').attributes('style')).toContain(
      'grid-column: 2',
    )
    expect(
      wrapper
        .get('[data-testid="heatmap-criterion-1"]')
        .find('[data-testid="heatmap-name-1"]')
        .exists(),
    ).toBe(true)
    expect(wrapper.get('[data-testid="heatmap-criterion-1"]').classes()).toContain('sticky')
    expect(wrapper.get('[data-testid="heatmap-criterion-1"]').classes()).toContain('top-[74px]')
    expect(wrapper.get('[data-testid="heatmap-report-label-report-a"]').classes()).toContain(
      'sticky',
    )
    expect(wrapper.get('[data-testid="heatmap-report-label-report-a"]').classes()).toContain(
      'left-0',
    )
    expect(wrapper.find('[data-testid="heatmap-toolbar"]').exists()).toBe(false)
    const reportHeader = wrapper.get('[data-testid="heatmap-report-header"]')
    expect(reportHeader.text()).toContain('Report')
    expect(reportHeader.find('[data-testid^="heatmap-score-summary-"]').exists()).toBe(false)
    expect(heatmap.findAll('[data-testid^="heatmap-cell-"]')).toHaveLength(6)
    expect(heatmap.text()).toContain('Evidence')
    expect(heatmap.text()).toContain('Accuracy')
    expect(
      wrapper.get('[data-testid="heatmap-cell-report-a-1"]').attributes('aria-label'),
    ).toContain('6/10')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).toContain(
      'heatmap-cell-loss-medium',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-2"]').classes()).toContain(
      'heatmap-cell-loss-none',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-c-1"]').classes()).toContain(
      'heatmap-cell-ungraded',
    )
    expect(heatmap.text()).not.toContain('C1')
    expect(heatmap.text()).not.toContain('C2')
    expect(heatmap.text()).not.toContain('Queued')
    expect(heatmap.text()).not.toContain('Grading')
    expect(heatmap.text()).not.toContain('Failed')
    expect(heatmap.text()).not.toContain('Low')
    expect(heatmap.text()).not.toContain('Medium')
    expect(heatmap.text()).not.toContain('High')
    const grid = wrapper.get('[data-testid="heatmap-grid"]')
    expect(grid.attributes('style')).toContain(
      'grid-template-columns: 40px repeat(2, minmax(0, 1fr)) 24px',
    )
    expect(grid.attributes('style')).toContain(
      'grid-template-rows: 24px 50px 30px repeat(3, minmax(16px, 1fr))',
    )
    expect(wrapper.get('[data-testid="heatmap-category-category-1"]').text()).toBe('analysis')
    expect(wrapper.get('[data-testid="heatmap-category-coverage-category-1"]').text()).toContain(
      '1/3 complete',
    )
    expect(grid.attributes('style')).not.toContain('min-width')
    expect(grid.classes()).toContain('gap-px')
    expect(wrapper.get('[data-testid="heatmap-scroll-region"]').classes()).toContain(
      'overflow-x-hidden',
    )
    expect(wrapper.get('[data-testid="global-rubric-heatmap"]').classes()).toContain('flex-col')
    expect(wrapper.get('[data-testid="heatmap-report-label-report-a"]').classes()).toContain('pr-1')
    const overviewHeader = wrapper.get('[data-testid="report-table-header"]')
    const firstOverviewRow = wrapper
      .get('[data-report-id="report-a"]')
      .get('[data-testid="global-overall-report-row"]')
    const overallGridClass =
      'grid-cols-[minmax(150px,220px)_76px_minmax(260px,1fr)_minmax(150px,0.65fr)_90px]'
    expect(wrapper.text()).not.toContain('All reports')
    expect(wrapper.find('[data-testid="report-sort-toggle"]').exists()).toBe(false)
    expect(overviewHeader.text()).toContain('Summary')
    expect(overviewHeader.text()).not.toContain('Main issues')
    expect(overviewHeader.text()).toContain('Status')
    expect(overviewHeader.classes()).toContain(overallGridClass)
    expect(firstOverviewRow.classes()).toContain(overallGridClass)
    expect(
      wrapper
        .get('[data-report-id="report-a"]')
        .get('[data-testid="global-report-analysis-summary"]')
        .text(),
    ).toContain('The evidence supports the main claim')
    expect(
      wrapper
        .get('[data-report-id="report-c"]')
        .get('[data-testid="global-report-analysis-summary"]')
        .text(),
    ).toBe('No report analysis available')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).not.toContain(
      'hover:-translate-y-px',
    )

    await wrapper.get('[data-testid="heatmap-cell-report-a-1"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(store.globalSelectedCriterionId).toBe('1')
    expect(store.globalFocusedReportId).toBe('report-a')
    expect(wrapper.find('[data-testid="global-rubric-heatmap"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="global-evidence-detail-strip"]')).toHaveLength(0)
    expect(wrapper.get('[data-report-id="report-a"]').classes()).toContain('interaction-soft')
    expect(wrapper.text()).not.toContain('View context')
    expect(wrapper.text()).not.toContain('Hide context')
    expect(wrapper.find('[data-testid="global-analysis-workspace"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="global-analysis-workspace"]').classes()).toContain(
      'xl:h-[clamp(360px,44vh,460px)]',
    )
    expect(wrapper.findAll('[data-testid="cohort-summary-metric"]')).toHaveLength(3)
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps long criterion titles in the R-number hover detail without expanding the header', () => {
    const store = prepareGlobalViewStore()
    store.criteriaSchema!.criteria[0]!.criteria_name = '实验过程完整性、清晰度与可复现性'
    const wrapper = mount(GlobalView)
    const gridStyle = wrapper.get('[data-testid="heatmap-grid"]').attributes('style') ?? ''
    const tooltip = wrapper.get('[data-testid="heatmap-tooltip-1"]')

    expect(wrapper.get('[data-testid="heatmap-code-1"]').text()).toBe('R1')
    expect(tooltip.text()).toContain('实验过程完整性、清晰度与可复现性')
    expect(tooltip.text()).toContain('Average points lost 2.5')
    expect(gridStyle).toContain('grid-template-rows: 24px 50px 30px')

    wrapper.unmount()
  })

  it('keeps heatmap criteria in rubric order instead of average-loss order', () => {
    const store = prepareGlobalViewStore()
    store.criteriaSchema!.criteria = [...store.criteriaSchema!.criteria].reverse()
    const wrapper = mount(GlobalView)

    expect(
      wrapper
        .findAll('[data-testid^="heatmap-criterion-"]')
        .map((item) => item.attributes('data-testid')),
    ).toEqual(['heatmap-criterion-2', 'heatmap-criterion-1'])
    expect(
      wrapper
        .findAll('[data-testid^="heatmap-cell-report-a-"]')
        .map((item) => item.attributes('data-testid')),
    ).toEqual(['heatmap-cell-report-a-2', 'heatmap-cell-report-a-1'])
  })

  it('reorders heatmap columns by category but keeps stable R labels within each group', () => {
    const store = prepareDenseGlobalViewStore(2, 3)
    store.criteriaSchema!.criteria[0]!.section = 'Information completeness'
    store.criteriaSchema!.criteria[1]!.section = 'Calculation quality'
    store.criteriaSchema!.criteria[2]!.section = 'Information completeness'
    const wrapper = mount(GlobalView)

    expect(
      wrapper
        .findAll('[data-testid^="heatmap-criterion-"]')
        .map((item) => item.attributes('data-testid')),
    ).toEqual(['heatmap-criterion-1', 'heatmap-criterion-3', 'heatmap-criterion-2'])
    expect(wrapper.get('[data-testid="heatmap-code-1"]').text()).toBe('R1')
    expect(wrapper.get('[data-testid="heatmap-code-3"]').text()).toBe('R3')
    expect(wrapper.get('[data-testid="heatmap-code-2"]').text()).toBe('R2')
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-3"]').attributes('style')).toContain(
      'grid-column: 3',
    )
    expect(wrapper.get('[data-testid="heatmap-cell-report-01-2"]').attributes('style')).toContain(
      'grid-column: 4',
    )
    expect(wrapper.get('[data-testid="heatmap-category-category-1"]').text()).toBe(
      'Information completeness',
    )
    expect(
      wrapper.get('[data-testid="heatmap-category-category-1"]').attributes('style'),
    ).toContain('grid-column: 2 / span 2')
  })

  it('uses equal-width histogram bars and highlights matching category cells', async () => {
    prepareGlobalViewStore()
    const wrapper = mount(GlobalView)
    const histogramButtons = wrapper.findAll(
      '[data-testid^="heatmap-category-histogram-category-1-bin-"]',
    )
    const visualBars = wrapper.findAll('[data-testid^="heatmap-category-bar-category-1-"]')
    const populatedBin = histogramButtons.find(
      (button) => button.attributes('data-bin-count') === '1',
    )!

    expect(histogramButtons).toHaveLength(8)
    expect(visualBars).toHaveLength(8)
    expect(visualBars.every((bar) => bar.classes().includes('w-[4px]'))).toBe(true)

    await populatedBin.trigger('mouseenter')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).toContain('ring-2')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-2"]').classes()).toContain('ring-2')
    expect(wrapper.get('[data-testid="heatmap-cell-report-b-1"]').classes()).not.toContain('ring-2')

    await populatedBin.trigger('click')
    await populatedBin.trigger('mouseleave')
    expect(populatedBin.attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).toContain('ring-2')

    await populatedBin.trigger('click')
    expect(populatedBin.attributes('aria-pressed')).toBe('false')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).not.toContain('ring-2')
  })

  it('keeps the compact matrix while restoring model-quality synthesis without top findings', async () => {
    const store = prepareGlobalViewStore()
    const wrapper = mount(GlobalView)
    const workspace = wrapper.get('[data-testid="global-analysis-workspace"]')

    expect(workspace.classes()).toContain('xl:grid-cols-[minmax(0,1fr)_minmax(520px,1fr)]')
    expect(workspace.classes()).toContain('xl:h-[clamp(360px,44vh,460px)]')
    expect(wrapper.find('[data-testid="cohort-insight-ribbon"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cohort-insight-connectors"]').exists()).toBe(false)
    await vi.waitFor(() =>
      expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain(
        'evidence quality is the clearest shared weakness',
      ),
    )
    expect(wrapper.findAll('[data-testid="cohort-summary-metric"]')).toHaveLength(3)
    expect(wrapper.findAll('[data-testid="cohort-summary-loss-row"]')).toHaveLength(2)
    expect(wrapper.get('[data-testid="cohort-summary-loss-grid"]').attributes('style')).toContain(
      'grid-auto-rows: calc(',
    )
    expect(wrapper.get('[data-testid="cohort-summary-loss-grid"]').classes()).toContain(
      'overflow-y-auto',
    )

    const overallBar = wrapper.findAll('[data-testid="cohort-summary-loss-row"]')[0]!
    await overallBar.trigger('mouseenter')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).toContain('ring-2')
    expect(wrapper.get('[data-testid="heatmap-cell-report-b-1"]').classes()).toContain('ring-2')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-2"]').classes()).not.toContain('ring-2')
    expect(wrapper.get('[data-report-id="report-a"]').classes()).toContain('interaction-outline')
    expect(wrapper.get('[data-report-id="report-b"]').classes()).toContain('interaction-outline')
    expect(wrapper.get('[data-report-id="report-c"]').classes()).not.toContain(
      'interaction-outline',
    )
    expect(wrapper.get('[data-testid="global-analysis-detail"]').text()).not.toContain(
      'highlighted · not filtered',
    )

    await overallBar.trigger('click')
    await overallBar.trigger('mouseleave')
    expect(wrapper.get('[data-testid="heatmap-cell-report-a-1"]').classes()).toContain('ring-2')
    expect(wrapper.get('[data-report-id="report-a"]').classes()).toContain('interaction-outline')
    expect(overallBar.attributes('aria-pressed')).toBe('true')

    await wrapper.get('[data-testid="heatmap-criterion-1"]').trigger('click')
    expect(store.globalSelectedCriterionId).toBe('1')
    await vi.waitFor(() =>
      expect(wrapper.get('[data-testid="cohort-summary-visualization"]').text()).toContain(
        'Claims are not fully connected to supporting evidence',
      ),
    )
    expect(wrapper.findAll('[data-testid="cohort-summary-bar-fill"]')).toHaveLength(1)
  })

  it('shows a full summary loader for an uncached criterion and reuses completed analysis', async () => {
    const synthesis = (criterionId: string | null, analysis: string): CohortSynthesis => ({
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      criterion_id: criterionId,
      projection_hash: 'projection-a',
      model_id: 'qa-model',
      language: 'en',
      analysis,
      insights: criterionId
        ? [
            {
              insight_id: `criterion-${criterionId}`,
              summary: 'Current criterion issue',
              criterion_ids: [criterionId],
              point_ids: [`${criterionId}.p1`],
              report_ids: ['report-a'],
              supporting_refs: [],
            },
          ]
        : [],
      generated_at: '2026-08-28T00:00:00Z',
    })
    let resolveCriterion!: (response: ApiResponse<CohortSynthesis>) => void
    const criterionRequest = new Promise<ApiResponse<CohortSynthesis>>((resolve) => {
      resolveCriterion = resolve
    })
    vi.mocked(api.getCohortSynthesis).mockImplementation((_caseId, payload) =>
      payload.criterion_id
        ? criterionRequest
        : Promise.resolve({ status: 'ok', data: synthesis(null, 'Overall analysis ready.') }),
    )
    const store = prepareGlobalViewStore()
    const wrapper = mount(GlobalView)

    await vi.waitFor(() =>
      expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain(
        'Overall analysis ready.',
      ),
    )
    await wrapper.get('[data-testid="heatmap-criterion-1"]').trigger('click')

    expect(store.globalSelectedCriterionId).toBe('1')
    expect(wrapper.find('[data-testid="cohort-summary-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="cohort-analysis"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cohort-summary-loss-row"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Overall analysis ready.')

    resolveCriterion({ status: 'ok', data: synthesis('1', 'Criterion analysis ready.') })
    await vi.waitFor(() =>
      expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain(
        'Criterion analysis ready.',
      ),
    )
    expect(wrapper.find('[data-testid="cohort-summary-loading"]').exists()).toBe(false)

    await wrapper.get('[data-testid="heatmap-clear-selection"]').trigger('click')
    await wrapper.get('[data-testid="heatmap-criterion-1"]').trigger('click')
    expect(wrapper.find('[data-testid="cohort-summary-loading"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain(
      'Criterion analysis ready.',
    )
  })

  it('clamps removed zoom levels to the remaining three card sizes', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalZoom(-10)
    expect(store.globalZoomByCase['case-a']).toBe(1)
    store.setGlobalZoom(10)
    expect(store.globalZoomByCase['case-a']).toBe(3)
  })

  it('uses immediate report-row positioning when reduced motion is requested', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn(() => ({
        matches: true,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    })
    prepareGlobalViewStore()
    const wrapper = mount(GlobalView)

    await wrapper.get('[data-testid="heatmap-cell-report-a-1"]').trigger('click')
    await vi.waitFor(() => expect(Element.prototype.scrollTo).toHaveBeenCalled())
    expect(Element.prototype.scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'auto' }),
    )
    wrapper.unmount()
  })

  it('keeps criterion analysis rows ordered without a duplicate context expansion', async () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const wrapper = mount(GlobalView, { attachTo: document.body })
    expect(
      wrapper
        .findAll('[data-testid="global-report-tile"]')
        .map((item) => item.attributes('data-report-id')),
    ).toEqual(['report-a', 'report-b', 'report-c'])
    expect(wrapper.findAll('[data-testid="global-criterion-card"]')).toHaveLength(0)
    expect(wrapper.get('[data-testid="heatmap-criterion-1"]').classes()).toContain(
      'interaction-soft',
    )
    expect(wrapper.get('[data-testid="heatmap-code-1"]').text()).toBe('R1')

    expect(store.globalSelectedCriterionId).toBe('1')
    expect(wrapper.get('[data-testid="heatmap-report-header"]').text()).not.toContain('Report')
    const reportCell = wrapper.get('[data-report-cell="report-a"]')
    expect(reportCell.find('[data-testid="global-evidence-detail-strip"]').exists()).toBe(false)
    expect(reportCell.text()).not.toContain('View context')
    expect(wrapper.text()).toContain('Grounded reason')
    expect(wrapper.get('[data-report-id="report-a"]').text()).toContain('Teacher')
    expect(wrapper.get('[data-report-id="report-b"]').text()).toContain('Model')

    expect(
      wrapper.find('[data-testid="global-analysis-detail"] section button.group').exists(),
    ).toBe(false)
    await wrapper.get('[data-testid="heatmap-clear-selection"]').trigger('click')
    expect(store.globalSelectedCriterionId).toBeNull()
    expect(wrapper.find('[data-testid^="heatmap-score-summary-"]').exists()).toBe(false)
    expect(
      wrapper
        .findAll('[data-testid="global-report-tile"]')
        .map((item) => item.attributes('data-report-id')),
    ).toEqual(['report-a', 'report-b', 'report-c'])
    wrapper.unmount()
  })

  it('uses verified color for complete report coverage instead of running status color', () => {
    prepareGlobalViewStore()
    const wrapper = mount(GlobalView)
    const completeProgress = wrapper
      .get('[data-report-id="report-a"]')
      .get('[data-testid="global-report-progress"]')
    const partialProgress = wrapper
      .get('[data-report-id="report-b"]')
      .get('[data-testid="global-report-progress"]')

    expect(completeProgress.classes()).toContain('bg-[var(--status-verified)]')
    expect(completeProgress.classes()).not.toContain('bg-[var(--status-running)]')
    expect(partialProgress.classes()).toContain('bg-[#82918b]')
    wrapper.unmount()
  })

  it('keeps semantic report tones stable when the interface language is Chinese', () => {
    setLocale('zh')
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const firstPoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    firstPoint.conflict = true
    const wrapper = mount(GlobalView)

    expect(
      wrapper
        .get('[data-report-id="report-a"]')
        .findAll('span')
        .some((element) => element.classes().includes('bg-[var(--status-conflict)]')),
    ).toBe(true)
    expect(wrapper.get('[data-report-id="report-b"]').text()).toContain('模型')
    expect(
      wrapper
        .get('[data-report-id="report-b"]')
        .findAll('span')
        .some((element) => element.classes().includes('bg-[var(--status-verified)]')),
    ).toBe(true)
    wrapper.unmount()
  })

  it('localizes the default overall inspector without relabeling it as a criterion', async () => {
    setLocale('zh')
    prepareGlobalViewStore()
    const wrapper = mount(GlobalView)
    const inspector = wrapper.get('[data-testid="cohort-summary-visualization"]')

    await vi.waitFor(() => expect(inspector.text()).toContain('总体分析'))
    expect(inspector.text()).toContain('总体')
    expect(inspector.text()).not.toContain('Overall')
    expect(inspector.text()).not.toContain('评分点分析')
    wrapper.unmount()
  })

  it('renders an ungraded criterion as neutral instead of review-needed', () => {
    const store = prepareGlobalViewStore()
    store.setGlobalSelectedCriterion('1')
    const wrapper = mount(GlobalView)
    const ungradedReport = wrapper.get('[data-report-id="report-c"]')
    const dotClasses = ungradedReport.findAll('span').flatMap((element) => element.classes())

    expect(dotClasses).toContain('bg-[var(--status-neutral)]')
    expect(dotClasses).not.toContain('bg-[var(--status-review)]')
    wrapper.unmount()
  })

  it('renders missing-content evidence as a dashed absence marker and explicit detail', async () => {
    const store = prepareGlobalViewStore()
    const absencePoint = store.alignment.criterion_global_summaries[0]!.points[0]!
    absencePoint.anchor_text = ''
    absencePoint.evidence_anchors = [
      {
        anchor_type: 'absence',
        expected_location: 'document:end',
        missing_item: 'limitations section',
      },
    ]
    store.setGlobalSelectedCriterion('1')
    const wrapper = mount(GlobalView, { attachTo: document.body })
    expect(wrapper.find('[data-testid="global-absence-evidence"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="global-missing-evidence"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('limitations section')
    wrapper.unmount()
  })

  it('shows automatic grading targets without scores as ungraded', () => {
    const store = prepareGlobalViewStore()
    store.alignment.auto_grade_jobs = [
      {
        report_name: 'report-c',
        criteria_id: '1',
        status: 'failed',
        criteria_file_name: 'rubric.json',
      },
      {
        report_name: 'report-c',
        criteria_id: '2',
        status: 'abstained',
        criteria_file_name: 'rubric.json',
      },
    ]
    const wrapper = mount(GlobalView)
    const failedCell = wrapper.get('[data-testid="heatmap-cell-report-c-1"]')
    const abstainedCell = wrapper.get('[data-testid="heatmap-cell-report-c-2"]')
    expect(failedCell.classes()).toContain('heatmap-cell-ungraded')
    expect(failedCell.attributes('aria-label')).toContain('not graded')
    expect(abstainedCell.classes()).toContain('heatmap-cell-ungraded')
    expect(abstainedCell.attributes('aria-label')).toContain('not graded')
  })

  it('shows queued and running targets without scores as ungraded', () => {
    const store = prepareGlobalViewStore()
    store.alignment.auto_grade_jobs = [
      {
        report_name: 'report-c',
        criteria_id: '1',
        status: 'pending',
        criteria_file_name: 'rubric.json',
      },
      {
        report_name: 'report-c',
        criteria_id: '2',
        status: 'running',
        criteria_file_name: 'rubric.json',
      },
    ]
    const wrapper = mount(GlobalView)
    const queuedCell = wrapper.get('[data-testid="heatmap-cell-report-c-1"]')
    const runningCell = wrapper.get('[data-testid="heatmap-cell-report-c-2"]')

    expect(queuedCell.classes()).toContain('heatmap-cell-ungraded')
    expect(queuedCell.attributes('aria-label')).toContain('not graded')
    expect(runningCell.classes()).toContain('heatmap-cell-ungraded')
    expect(runningCell.attributes('aria-label')).toContain('not graded')
  })

  it('keeps teacher and model score gradients visible while an automatic batch is active', () => {
    const store = prepareGlobalViewStore()
    const active = batch('running')
    active.criteria = [
      { criterion_id: '1', criterion_name: 'Evidence', max_score: 10 },
      { criterion_id: '2', criterion_name: 'Accuracy', max_score: 5 },
    ]
    active.targets = [
      { report_name: 'report-a', criterion_id: '1', status: 'pending' },
      { report_name: 'report-a', criterion_id: '2', status: 'running' },
      { report_name: 'report-c', criterion_id: '1', status: 'pending' },
    ]
    store.alignment.auto_grade_batches = [active]

    const wrapper = mount(GlobalView)
    const teacherCell = wrapper.get('[data-testid="heatmap-cell-report-a-1"]')
    const modelCell = wrapper.get('[data-testid="heatmap-cell-report-a-2"]')
    const ungradedCell = wrapper.get('[data-testid="heatmap-cell-report-c-1"]')

    expect(teacherCell.classes()).toContain('heatmap-cell-loss-medium')
    expect(teacherCell.attributes('aria-label')).toContain('6/10')
    expect(modelCell.classes()).toContain('heatmap-cell-loss-none')
    expect(modelCell.attributes('aria-label')).toContain('5/5')
    expect(ungradedCell.classes()).toContain('heatmap-cell-ungraded')
    expect(ungradedCell.attributes('aria-label')).toContain('not graded')
  })

  it('confirms calibration from the Global View batch banner', async () => {
    const store = prepareGlobalViewStore()
    store.alignment.auto_grade_batches = [batch('awaiting_calibration')]
    const confirm = vi.spyOn(store, 'confirmAutoGradeBatch').mockResolvedValue(batch('queued'))
    const wrapper = mount(GlobalView)

    expect(wrapper.get('[data-testid="global-auto-grade-batch-banner"]').text()).toContain(
      'Ready to start',
    )
    await wrapper.get('[data-testid="global-confirm-auto-grade-batch"]').trigger('click')

    expect(confirm).toHaveBeenCalledWith('batch-1')
  })

  it('requires confirmation before stopping auto-grading from Global View', async () => {
    const store = prepareGlobalViewStore()
    store.alignment.auto_grade_batches = [batch('running')]
    const cancel = vi.spyOn(store, 'cancelAutoGradeBatch').mockResolvedValue(batch('cancelling'))
    const confirmDialog = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(GlobalView)

    await wrapper.get('[data-testid="global-cancel-auto-grade-batch"]').trigger('click')

    expect(confirmDialog).toHaveBeenCalledOnce()
    expect(cancel).toHaveBeenCalledWith('batch-1')
  })
})
