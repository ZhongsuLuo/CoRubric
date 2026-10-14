import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { CriteriaItem, CriterionConsistencyState, GradingItem } from '../types/schema'
import CriteriaCardSummary from './CriteriaCardSummary.vue'

const item: CriteriaItem = {
  criteria_id: '1',
  criteria_name: 'Analysis',
  description: 'Evaluate the analysis.',
  original_description: 'Evaluate the analysis.',
  section: 'Analysis',
  max_score: 10,
  few_shot_examples: [],
  scoring_points: [],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
}

const grading: GradingItem = {
  ...item,
  section: 'Analysis',
  criteria_score: 8,
  file_original_content: '',
  target_section: 'Analysis',
  score_reason: 'Mostly complete.',
  graded_by: 'model',
  acceptance_status: 'accepted_model',
}

const consistency = (state: CriterionConsistencyState['state']): CriterionConsistencyState => ({
  record_key: 'criteria.json::1',
  criteria_file_name: 'criteria.json',
  criterion_id: '1',
  criteria_id: '1',
  state,
  state_source: 'evidence',
  agreement_count: 3,
  correction_count: 1,
  smoothed_agreement: 0.75,
  threshold: 0.7,
  review_rate: 0.25,
})

describe('CriteriaCardSummary semantic colors', () => {
  it('uses model provenance and review status tokens for unreliable consistency', () => {
    const wrapper = mount(CriteriaCardSummary, {
      props: { item, grading, consistency: consistency('unreliable') },
    })

    expect(wrapper.get('[data-testid="criterion-provenance-1"]').classes()).toContain(
      'source-badge-model',
    )
    expect(wrapper.get('[role="meter"][aria-label="Agreement rate"] div').classes()).toContain(
      'status-fill-review',
    )
    expect(wrapper.html()).toContain('text-[var(--status-review)]')
  })

  it('uses the verified token for reliable consistency', () => {
    const wrapper = mount(CriteriaCardSummary, {
      props: { item, grading, consistency: consistency('reliable') },
    })

    expect(wrapper.get('[role="meter"][aria-label="Agreement rate"] div').classes()).toContain(
      'status-fill-verified',
    )
    expect(wrapper.html()).toContain('text-[var(--status-verified)]')
  })
})
