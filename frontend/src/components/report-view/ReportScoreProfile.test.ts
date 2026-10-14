import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type {
  CriteriaItem,
  CriterionProposalItem,
  GradingItem,
  HighlightItem,
} from '../../types/schema'
import ReportScoreProfile from './ReportScoreProfile.vue'

const criterion = (
  id: string,
  name: string,
  maximum: number,
  section = 'Analysis',
): CriteriaItem => ({
  criteria_id: id,
  criteria_name: name,
  description: `${name} description`,
  original_description: `${name} description`,
  section,
  max_score: maximum,
  few_shot_examples: [],
  scoring_points: [
    {
      point_id: `${id}.p1`,
      summary: `${name} point`,
      max_deduction: maximum,
      deduction_rules: [{ rule_id: `${id}.p1.r1`, condition: 'Missing', deduct: maximum }],
    },
  ],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
})

const grading = (item: CriteriaItem, score: number): GradingItem => ({
  ...item,
  section: item.section || '',
  criteria_score: score,
  file_original_content: '',
  target_section: item.section || null,
  score_reason: `${item.criteria_name} reason`,
  matched_scoring_points: score < item.max_score ? [`${item.criteria_id}.p1`] : [],
  scoring_point_scores: [{ point_id: `${item.criteria_id}.p1`, score, max_score: item.max_score }],
})

const proposal = (
  id: string,
  type: CriterionProposalItem['proposal_type'],
  suggested: CriteriaItem,
  targetId: string | null,
): CriterionProposalItem => ({
  proposal_id: id,
  proposal_version: 1,
  case_id: 'case-1',
  proposal_type: type,
  target_criterion_id: targetId,
  source_implicit_grade_ids: [`implicit-${id}`],
  source_grade_digests: [`digest-${id}`],
  source_grade_assignments: [],
  status: 'pending',
  criteria_file_name: 'criteria.json',
  suggested_criterion: suggested,
  rationale: `${type} rationale`,
  confirmed_event_ids: [],
  created_at: '2026-08-25T00:00:00Z',
  updated_at: '2026-08-25T00:00:00Z',
})

const evidence = (criteriaId: string): HighlightItem => ({
  id: `evidence-${criteriaId}`,
  report_id: 'report-1',
  criteria_id: criteriaId,
  score: 0,
  reason: '',
  source: 'llm',
  color: 'green',
  anchor: { text: `${criteriaId} evidence` },
})

const mountProfile = () => {
  const first = criterion('1', 'Title', 4)
  const second = criterion('2', 'Calculation', 4)
  const update = proposal('update', 'update_criterion', { ...second, description: 'Updated' }, '2')
  const created = proposal('create', 'create_criterion', criterion('3', 'Dilution', 4), null)
  const wrapper = mount(ReportScoreProfile, {
    props: {
      criteria: [first, second],
      gradings: [grading(first, 4), grading(second, 2)],
      proposals: [update, created],
      highlights: [evidence('1'), evidence('2')],
      reportScore: 68,
      baseScore: 100,
      activeCriteriaId: '1',
      conflictCriteriaIds: ['2'],
      reviewCriteriaIds: ['1'],
    },
  })
  return { wrapper, first, second, update, created }
}

describe('ReportScoreProfile', () => {
  it('shows earned score segments, evidence, and both proposal types', () => {
    const { wrapper } = mountProfile()

    expect(wrapper.get('[data-testid="report-score-profile"]').text()).toContain('68/100')
    expect(wrapper.find('[data-testid="score-profile-criterion-1"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="score-profile-criterion-1"]').text()).toContain('4/4')
    expect(wrapper.get('[data-testid="score-profile-criterion-1"]').text()).not.toContain('1')
    expect(wrapper.get('[data-testid="score-profile-proposal-update"]').text()).toContain('Update')
    expect(wrapper.get('[data-testid="score-profile-proposal-create"]').text()).toContain('New')
    expect(wrapper.findAll('.evidence-marker-model')).toHaveLength(2)
    expect(wrapper.html()).toContain('bg-[var(--status-review)]')
    expect(wrapper.find('.status-badge-conflict').exists()).toBe(true)
    expect(wrapper.find('.status-badge-review').exists()).toBe(true)
  })

  it('selects the target row and confirms an update proposal in place', async () => {
    const { wrapper, update } = mountProfile()

    await wrapper.get('[data-testid="score-profile-proposal-update"]').trigger('click')
    expect(wrapper.emitted('selectCriterion')?.slice(-1)[0]).toEqual(['2'])
    expect(wrapper.get('[data-testid="score-profile-proposal-detail"]').text()).toContain(
      'Merge into existing criterion',
    )

    await wrapper.get('[data-testid="score-profile-confirm-proposal"]').trigger('click')
    expect(wrapper.emitted('confirmProposal')?.slice(-1)[0]).toEqual([update])
  })

  it('shows a create proposal as a ghost column and confirms it without selecting a row', async () => {
    const { wrapper, created } = mountProfile()

    await wrapper.get('[data-testid="score-profile-proposal-create"]').trigger('click')
    expect(wrapper.get('[data-testid="score-profile-proposal-detail"]').text()).toContain(
      'Create new criterion',
    )
    expect(wrapper.emitted('selectCriterion')).toBeUndefined()

    await wrapper.get('[data-testid="score-profile-confirm-proposal"]').trigger('click')
    expect(wrapper.emitted('confirmProposal')?.slice(-1)[0]).toEqual([created])
  })

  it('selects a criterion from its score column', async () => {
    const { wrapper } = mountProfile()

    await wrapper.get('[data-testid="score-profile-criterion-2"]').trigger('click')
    expect(wrapper.emitted('selectCriterion')?.slice(-1)[0]).toEqual(['2'])
  })
})
