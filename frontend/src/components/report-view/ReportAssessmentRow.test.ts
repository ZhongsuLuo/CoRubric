import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { CriteriaItem, GradingItem, HighlightItem } from '../../types/schema'
import ReportAssessmentRow from './ReportAssessmentRow.vue'

const item: CriteriaItem = {
  criteria_id: 'c1',
  criteria_name: 'Instruments and reagents',
  description: 'List the required instruments and reagents.',
  original_description: 'List the required instruments and reagents.',
  max_score: 4,
  section: 'Method',
  few_shot_examples: [],
  scoring_points: [
    {
      point_id: 'c1.p1',
      summary: 'List the main instruments',
      max_deduction: 2,
      deduction_rules: [{ rule_id: 'c1.p1.r1', condition: 'Missing', deduct: 2 }],
    },
    {
      point_id: 'c1.p2',
      summary: 'State reagent specifications',
      max_deduction: 2,
      deduction_rules: [{ rule_id: 'c1.p2.r1', condition: 'Missing', deduct: 2 }],
    },
  ],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
}

const grading: GradingItem = {
  ...item,
  criteria_score: 3,
  file_original_content: '',
  section: 'Method',
  target_section: 'Method',
  score_reason: 'One specification is missing.',
  matched_scoring_points: ['c1.p1'],
  scoring_point_scores: [
    { point_id: 'c1.p1', score: 2, max_score: 2 },
    { point_id: 'c1.p2', score: 1, max_score: 2 },
  ],
  graded_by: 'model',
}

const segments: HighlightItem[] = [
  {
    id: 'evidence-1',
    report_id: 'report-1',
    criteria_id: 'c1',
    score: 3,
    max_score: 4,
    reason: 'One specification is missing.',
    source: 'llm',
    color: 'blue',
    anchor: { text: 'Atomic absorption spectrometer' },
  },
  {
    id: 'evidence-2',
    report_id: 'report-1',
    criteria_id: 'c1',
    score: 3,
    max_score: 4,
    reason: 'One specification is missing.',
    source: 'llm',
    color: 'blue',
    anchor: { text: 'Mg standard solution' },
  },
]

describe('ReportAssessmentRow', () => {
  it('renders a table row with score, scoring-point contributions, and evidence excerpts', () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    expect(wrapper.get('[data-testid="criterion-score-visual-c1"]').text()).toContain('3/4')
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').text()).toContain(
      'List the main instruments',
    )
    expect(wrapper.text()).toContain('Atomic absorption spectrometer')
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').text()).not.toContain(
      'Atomic absorption spectrometer',
    )
    expect(
      wrapper
        .findAll('[data-criterion-connection-highlight-id]')
        .map((node) => node.attributes('data-criterion-connection-highlight-id')),
    ).toEqual(['evidence-1', 'evidence-2'])
    expect(wrapper.find('[data-evidence-connection-id]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').text()).not.toContain('3/4')
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').text()).not.toContain('c1.p1')
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').text()).not.toContain(
      'rules triggered',
    )
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').text()).not.toContain('Missing')
    expect(wrapper.get('[data-testid="assessment-detail-c1"]').classes()).toContain('border-b-2')
  })

  it('gives the rationale more horizontal space than the scoring-point controls', () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    expect(wrapper.get('[data-testid="score-rationale-layout"]').classes()).toContain(
      'grid-cols-[minmax(15rem,2fr)_minmax(0,3fr)]',
    )
    expect(wrapper.get('[data-testid="score-rationale-layout"]').classes()).toContain(
      'items-stretch',
    )
    expect(wrapper.get('[data-testid="score-column-header"]').classes()).toContain('min-h-12')
    expect(wrapper.get('[data-testid="rationale-column-header"]').classes()).toContain('min-h-12')
    expect(
      wrapper
        .get('[data-testid="primary-grade-action-slot"]')
        .element.closest('[data-testid="rationale-column-header"]'),
    ).not.toBeNull()
  })

  it('lets the criterion arrow request expansion and collapse', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    const toggle = wrapper.get('[data-testid="toggle-criterion-c1"]')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    await toggle.trigger('click')
    expect(wrapper.emitted('toggle')).toEqual([['c1']])

    await wrapper.setProps({ active: false })
    expect(wrapper.find('[data-testid="assessment-detail-c1"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="toggle-criterion-c1"]').attributes('aria-expanded')).toBe(
      'false',
    )
  })

  it('keeps rule state internal and clears it when the teacher changes a point score', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: {
        item,
        active: true,
        evidenceSegments: segments,
        grading: {
          ...grading,
          criteria_score: 2,
          matched_scoring_points: ['c1.p2'],
          matched_deduction_rules: ['c1.p2.r1'],
          scoring_point_scores: [
            { point_id: 'c1.p1', score: 2, max_score: 2 },
            { point_id: 'c1.p2', score: 0, max_score: 2 },
          ],
        },
      },
    })

    expect(wrapper.text()).not.toContain('c1.p2.r1')
    expect(wrapper.text()).not.toContain('rules triggered')
    expect(wrapper.text()).not.toContain('Missing')

    await wrapper.get('[data-testid="inline-point-score-c1.p2"]').setValue('1')
    await wrapper.get('[data-testid="save-inline-grade-c1"]').trigger('click')

    expect(wrapper.emitted('save-grade')?.[0]?.[0]).toMatchObject({
      score: 3,
      matchedDeductionRules: [],
    })
  })

  it('keeps scoring points compact and expands only the rationale', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    expect(wrapper.get('[data-testid="scoring-ledger-point-c1.p1"]').classes()).toContain('py-1.5')
    expect(wrapper.find('[data-testid="scoring-point-details-c1.p1"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Deduction rules')
    expect(wrapper.text()).not.toContain('c1.p1.r1')

    await wrapper.get('[data-testid="toggle-scoring-point-details-c1.p1"]').trigger('click')

    expect(wrapper.get('[data-testid="scoring-point-details-c1.p1"]').text()).toContain(
      'No point rationale yet',
    )
    expect(wrapper.text()).not.toContain('Deduction rules')
  })

  it('expands an optional point-specific rationale without copying the criterion summary', async () => {
    const pointReason = 'The instrument list is complete and satisfies this scoring point.'
    const wrapper = mount(ReportAssessmentRow, {
      props: {
        item,
        grading: {
          ...grading,
          scoring_point_scores: [
            { point_id: 'c1.p1', score: 2, max_score: 2, reason: pointReason },
            {
              point_id: 'c1.p2',
              score: 1,
              max_score: 2,
              reason: 'The reagent concentration is not stated.',
            },
          ],
        },
        active: true,
        evidenceSegments: segments,
      },
    })

    expect(wrapper.get('[data-testid="toggle-scoring-point-details-c1.p1"]').text()).toContain(
      pointReason,
    )
    await wrapper.get('[data-testid="toggle-scoring-point-details-c1.p1"]').trigger('click')
    expect(wrapper.get('[data-testid="scoring-point-details-c1.p1"]').text()).toContain(pointReason)
    expect(wrapper.get('textarea').element.value).toBe('One specification is missing.')
  })

  it('keeps multi-segment navigation and rubric editing inside the selected table row', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: false, evidenceSegments: segments },
    })

    await wrapper.get('[data-testid="evidence-segment-c1-2"]').trigger('click')
    await wrapper.get('[data-testid="edit-criterion-c1"]').trigger('click')

    expect(wrapper.emitted('evidence-select')).toEqual([
      [{ criteriaId: 'c1', highlightId: 'evidence-2' }],
    ])
    expect(wrapper.emitted('select')).toEqual([['c1']])
    await wrapper.setProps({ active: true })
    expect(wrapper.get('[data-testid="inline-rubric-editor-c1"]').isVisible()).toBe(true)
    await wrapper.get('[data-testid="advanced-rubric-edit-c1"]').trigger('click')
    expect(wrapper.emitted('edit')).toEqual([['c1']])
    expect(wrapper.findAll('[data-criterion-connection-highlight-id]')).toHaveLength(2)
  })

  it('edits only scoring-point content and scores in the inline rubric editor', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    await wrapper.get('[data-testid="assessment-mode-rubric-c1"]').trigger('click')
    expect(wrapper.find('[data-testid="rubric-allocation-rail"]').exists()).toBe(true)

    expect(wrapper.text()).not.toContain('c1.p1')
    expect(wrapper.text()).not.toContain('Deduction rules')
    await wrapper.get('[data-testid="rubric-point-content-c1.p1"]').setValue('List all equipment')
    await wrapper.get('[data-testid="rubric-point-cap-c1.p1"]').setValue('1')
    expect(wrapper.get('[data-testid="rubric-budget-status"]').text()).toContain('3/4')
    expect(wrapper.get('[data-testid="save-inline-rubric-c1"]').attributes('disabled')).toBe('')

    await wrapper.get('[data-testid="rubric-point-cap-c1.p2"]').setValue('3')
    expect(wrapper.get('[data-testid="rubric-budget-status"]').text()).toContain('4/4')
    await wrapper.get('[data-testid="save-inline-rubric-c1"]').trigger('click')

    expect(wrapper.emitted('save-rubric')?.[0]?.[0]).toMatchObject({
      criterion: {
        scoring_points: [
          {
            point_id: 'c1.p1',
            summary: 'List all equipment',
            max_deduction: 1,
            deduction_rules: [{ condition: 'List all equipment', deduct: 1 }],
          },
          {
            point_id: 'c1.p2',
            max_deduction: 3,
            deduction_rules: [{ deduct: 3 }],
          },
        ],
      },
    })
  })

  it('hides the allocation rail when a criterion has only one scoring point', async () => {
    const singlePointItem: CriteriaItem = {
      ...item,
      max_score: 2,
      scoring_points: [item.scoring_points[0]!],
    }
    const wrapper = mount(ReportAssessmentRow, {
      props: { item: singlePointItem, active: true },
    })

    await wrapper.get('[data-testid="assessment-mode-rubric-c1"]').trigger('click')

    expect(wrapper.find('[data-testid="rubric-allocation-rail"]').exists()).toBe(false)
  })

  it('does not create an external connection for absence evidence', () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: {
        item,
        active: true,
        grading: {
          ...grading,
          evidence_anchors: [
            { anchor_type: 'absence', text: '', missing_item: 'Reagent concentration' },
          ],
        },
        evidenceSegments: [],
      },
    })

    expect(wrapper.text()).toContain('Reagent concentration')
    expect(wrapper.find('[data-criterion-connection-highlight-id]').exists()).toBe(false)
  })

  it('emits a structured teacher-grade draft from the inline score workspace', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    await wrapper.get('[data-testid="inline-point-score-c1.p2"]').setValue('2')
    await wrapper.get('[data-testid="save-inline-grade-c1"]').trigger('click')

    expect(wrapper.emitted('save-grade')?.[0]?.[0]).toMatchObject({
      criteriaId: 'c1',
      score: 4,
      anchorText: 'Atomic absorption spectrometer',
      scoringPointScores: [
        { point_id: 'c1.p1', score: 2, max_score: 2 },
        { point_id: 'c1.p2', score: 2, max_score: 2 },
      ],
    })
  })

  it('updates a scoring-point value by clicking the score visualization', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    await wrapper.get('[data-testid="inline-point-score-segment-c1.p2-2"]').trigger('click')

    expect(
      (wrapper.get('[data-testid="inline-point-score-c1.p2"]').element as HTMLInputElement).value,
    ).toBe('2')
    expect(wrapper.text()).toContain('3 → 4')
    expect(wrapper.find('[data-testid="model-draft-delta-c1.p2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="manual-adjustment-c1.p2"]').exists()).toBe(false)
  })

  it('uses a compact score input with a visible maximum', () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    const input = wrapper.get('[data-testid="inline-point-score-c1.p2"]')
    expect(input.classes()).toEqual(
      expect.arrayContaining(['score-value-input', 'h-6', 'w-8', 'text-center']),
    )
    expect(input.attributes('max')).toBe('2')
    expect(input.element.parentElement?.textContent?.trim()).toBe('/ 2')
  })

  it('removes score boilerplate from the editable rationale without creating a draft', () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: {
        item,
        grading: {
          ...grading,
          score_reason: 'The formula is correct. Earned 3.0 out of 4.0。',
        },
        active: true,
        canAcceptModelGrade: true,
        evidenceSegments: segments,
      },
    })

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(
      'The formula is correct.',
    )
    expect(wrapper.find('[data-testid="accept-model-pregrade-c1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="unsaved-teacher-draft"]').exists()).toBe(false)
  })

  it('shows exactly one model-accept or teacher-save action for the current draft state', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: {
        item,
        grading,
        active: true,
        canAcceptModelGrade: true,
        evidenceSegments: segments,
      },
    })

    expect(wrapper.find('[data-testid="accept-model-pregrade-c1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="save-inline-grade-c1"]').exists()).toBe(false)
    expect(
      wrapper
        .get('[data-testid="accept-model-pregrade-c1"]')
        .element.parentElement?.getAttribute('data-testid'),
    ).toBe('primary-grade-action-slot')

    await wrapper.get('[data-testid="inline-point-score-segment-c1.p2-2"]').trigger('click')

    expect(wrapper.find('[data-testid="accept-model-pregrade-c1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="save-inline-grade-c1"]').exists()).toBe(true)
    expect(
      wrapper
        .get('[data-testid="save-inline-grade-c1"]')
        .element.parentElement?.getAttribute('data-testid'),
    ).toBe('primary-grade-action-slot')
    expect(wrapper.get('textarea').classes()).toContain('overflow-y-hidden')
  })

  it('hides teacher-save until an ungraded criterion receives teacher input', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, active: true, evidenceSegments: [] },
    })

    expect(wrapper.find('[data-testid="save-inline-grade-c1"]').exists()).toBe(false)

    await wrapper.get('[data-testid="inline-point-score-segment-c1.p1-1"]').trigger('click')

    expect(wrapper.find('[data-testid="save-inline-grade-c1"]').exists()).toBe(true)
  })

  it('starts evidence reselection with or without an existing evidence segment', async () => {
    const wrapper = mount(ReportAssessmentRow, {
      props: { item, grading, active: true, evidenceSegments: segments },
    })

    const reselectEvidenceButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Reselect evidence'))
    await reselectEvidenceButton?.trigger('click')
    expect(wrapper.emitted('evidence-adjust')).toEqual([
      [{ criteriaId: 'c1', highlightId: 'evidence-1' }],
    ])

    await wrapper.setProps({ evidenceSegments: [] })
    const addEvidenceButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Add evidence'))
    expect(addEvidenceButton?.attributes('disabled')).toBeUndefined()
    await addEvidenceButton?.trigger('click')
    expect(wrapper.emitted('evidence-adjust')?.[1]).toEqual([
      { criteriaId: 'c1', highlightId: null },
    ])
  })
})
