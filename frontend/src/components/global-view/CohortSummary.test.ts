import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { point, reports, summary } from '../globalViewTestFixture'
import CohortSummary from './CohortSummary.vue'

const evidenceSummary = summary('1', 10, 2.5, [
  point('report-a', '1', 6, 10),
  point('report-b', '1', 9, 10, 'model'),
])
const accuracySummary = summary('2', 5, 0, [point('report-a', '2', 5, 5, 'model')])
const commonProps = {
  reportCount: reports.length,
  reportSummaries: reports,
  summaries: [evidenceSummary, accuracySummary],
  criterionNames: { '1': 'Evidence', '2': 'Accuracy' },
  analysis: '',
  insights: [],
  synthesisLoading: false,
  synthesisError: '',
  activeFocusId: null,
  pinnedFocusId: null,
}

describe('CohortSummary', () => {
  it('summarizes the overall projection without model-generated findings', () => {
    const wrapper = mount(CohortSummary, {
      props: {
        ...commonProps,
        selectedCriterionId: null,
        selectedCriterionName: 'Overall',
      },
    })

    expect(
      wrapper
        .get('[data-testid="cohort-summary-header"]')
        .findAll('[data-testid="cohort-summary-metric"]'),
    ).toHaveLength(3)
    expect(wrapper.text()).toContain('96.5')
    expect(wrapper.text()).toContain('1/3')
    expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain('Evidence')
    expect(wrapper.findAll('[data-testid="cohort-summary-loss-row"]')).toHaveLength(2)
    expect(wrapper.get('[data-testid="cohort-analysis-chart"] > div').classes()).toContain(
      'grid-cols-2',
    )
    expect(wrapper.findAll('[data-testid="cohort-summary-bar-fill"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="cohort-summary-bar-fill"]')[0]!.attributes('style')).toBe(
      'width: 100%;',
    )
    expect(wrapper.findAll('[data-testid="cohort-summary-bar-fill"]')[1]!.attributes('style')).toBe(
      'width: 0%;',
    )
    expect(wrapper.get('[data-testid="cohort-summary-row-meta"]').text()).toContain('−2.5 avg.')
    expect(wrapper.text()).toContain('Teacher grades · 1')
    expect(wrapper.text()).toContain('Model grades · 2')
    expect(wrapper.find('[data-testid="cohort-insight-bar"]').exists()).toBe(false)
  })

  it('replaces the complete summary surface while synthesis is loading', () => {
    const wrapper = mount(CohortSummary, {
      props: {
        ...commonProps,
        selectedCriterionId: '1',
        selectedCriterionName: 'Evidence',
        analysis: 'Previous criterion analysis must not remain visible.',
        insights: [
          {
            insight_id: 'previous-insight',
            summary: 'Previous criterion issue',
            criterion_ids: ['2'],
            point_ids: ['2.p1'],
            report_ids: ['report-a'],
            supporting_refs: [],
          },
        ],
        synthesisLoading: true,
      },
    })

    expect(wrapper.get('[data-testid="cohort-summary-visualization"]').attributes('aria-busy')).toBe(
      'true',
    )
    expect(wrapper.get('[data-testid="cohort-summary-loading"]').text()).toContain(
      'Analyzing current cohort…',
    )
    expect(wrapper.find('[data-testid="cohort-summary-header"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cohort-analysis"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cohort-summary-loss-row"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Previous criterion analysis')
    expect(wrapper.text()).not.toContain('Previous criterion issue')
  })

  it('emits hover and pinned chart focus for matrix highlighting', async () => {
    const wrapper = mount(CohortSummary, {
      props: {
        ...commonProps,
        selectedCriterionId: null,
        selectedCriterionName: 'Overall',
      },
    })
    const row = wrapper.findAll('[data-testid="cohort-summary-loss-row"]')[0]!

    await row.trigger('mouseenter')
    expect(wrapper.emitted('highlight')?.[0]?.[0]).toMatchObject({
      id: '1',
      criterionIds: ['1'],
      reportIds: ['report-a', 'report-b'],
    })

    await row.trigger('click')
    expect(wrapper.emitted('toggleFocus')?.[0]?.[0]).toMatchObject({
      id: '1',
      criterionIds: ['1'],
      reportIds: ['report-a', 'report-b'],
    })
  })

  it('switches to deterministic scoring-point loss when a criterion is selected', () => {
    const wrapper = mount(CohortSummary, {
      props: {
        ...commonProps,
        selectedCriterionId: '1',
        selectedCriterionName: 'Evidence',
      },
    })

    expect(wrapper.text()).toContain('Evidence')
    expect(wrapper.text()).toContain('7.5/10')
    expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain(
      '2 of 3 reports contribute to Evidence',
    )
    expect(wrapper.text()).toContain('Point 1')
    expect(wrapper.text()).toContain('2/2')
    expect(wrapper.findAll('[data-testid="cohort-summary-bar-fill"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('Teacher grades · 1')
    expect(wrapper.text()).toContain('Model grades · 1')
  })

  it('uses model synthesis text and grounded pattern bars when available', () => {
    const wrapper = mount(CohortSummary, {
      props: {
        ...commonProps,
        selectedCriterionId: '1',
        selectedCriterionName: 'Evidence',
        analysis:
          'The cohort repeatedly loses evidence credit because claims are not fully supported.',
        insights: [
          {
            insight_id: 'unsupported-claims',
            summary: 'Claims lack complete supporting evidence',
            criterion_ids: ['1'],
            point_ids: ['1.p1'],
            report_ids: ['report-a', 'report-b'],
            supporting_refs: [
              { report_id: 'report-a', event_id: 'event-a' },
              { report_id: 'report-a', event_id: 'event-a' },
            ],
          },
        ],
      },
    })

    expect(wrapper.get('[data-testid="cohort-analysis"]').text()).toContain(
      'The cohort repeatedly loses evidence credit',
    )
    expect(wrapper.get('[data-testid="cohort-summary-loss-row"]').text()).toContain(
      'Claims lack complete supporting evidence',
    )
    expect(wrapper.get('[data-testid="cohort-summary-loss-row"]').text()).toContain('2/3')
    expect(wrapper.get('[data-testid="cohort-summary-loss-row"]').text()).toContain(
      '1 evidence refs',
    )
  })

  it('removes legacy average-loss metrics from synthesis labels', () => {
    const wrapper = mount(CohortSummary, {
      props: {
        ...commonProps,
        selectedCriterionId: '1',
        selectedCriterionName: 'Evidence',
        insights: [
          {
            insight_id: 'legacy-loss-label',
            summary: '实验原理概念错误：平均失分3.14分',
            criterion_ids: ['1'],
            point_ids: ['1.p1'],
            report_ids: ['report-a'],
            supporting_refs: [{ report_id: 'report-a', event_id: 'event-a' }],
          },
        ],
      },
    })

    expect(wrapper.get('[data-testid="cohort-summary-row-label"]').text()).toBe(
      '实验原理概念错误',
    )
    expect(wrapper.get('[data-testid="cohort-summary-loss-row"]').text()).not.toContain(
      '平均失分3.14分',
    )
  })
})
