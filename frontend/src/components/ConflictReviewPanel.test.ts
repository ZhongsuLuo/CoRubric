import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useGradingStore } from '../stores/grading'
import ConflictReviewPanel from './ConflictReviewPanel.vue'

beforeEach(() => setActivePinia(createPinia()))

const conflict = {
  conflict_id: 'conflict-1',
  criteria_id: 'c1',
  conflict_type: 'teacher_teacher',
  reason: 'The two teacher grades represent the same scoring pattern.',
  teacher_event_ids: ['event-current', 'event-alternative'],
  current_event_id: 'event-current',
  alternative_event_id: 'event-alternative',
  current_pattern: {
    pattern_id: 'pattern-current',
    event_ids: ['event-current'],
    representative_event_id: 'event-current',
    event_id: 'event-current',
    report_name: 'report-a',
    grader_type: 'teacher',
    criteria_score: 2,
    score_reason: 'Established teacher judgment.',
    anchor_text: 'same evidence pattern',
    scoring_point_scores: [{ point_id: 'c1.p1', score: 2 }],
  },
  alternative_pattern: {
    pattern_id: 'pattern-alternative',
    event_ids: ['event-alternative'],
    representative_event_id: 'event-alternative',
    event_id: 'event-alternative',
    report_name: 'report-b',
    grader_type: 'teacher',
    criteria_score: 1,
    score_reason: 'New teacher judgment.',
    anchor_text: 'same evidence pattern',
    scoring_point_scores: [{ point_id: 'c1.p1', score: 1 }],
  },
}

describe('ConflictReviewPanel', () => {
  it('shows both teacher patterns without internal identifiers or legacy model actions', () => {
    const wrapper = mount(ConflictReviewPanel, {
      props: { inline: true, reportName: 'report-a', conflicts: [conflict] },
    })

    expect(wrapper.get('[data-testid="inline-conflict-review"]').text()).toContain('report-a')
    expect(wrapper.text()).toContain('report-b')
    expect(wrapper.text()).toContain('2')
    expect(wrapper.text()).toContain('1')
    expect(wrapper.text()).not.toContain('conflict-1')
    expect(wrapper.text()).not.toContain('Request reassessment')
    expect(wrapper.text()).not.toContain('Accept model recommendation')
  })

  it.each([
    ['current', 'pattern-current'],
    ['alternative', 'pattern-alternative'],
  ] as const)('submits the %s pattern as the sole conflict decision', async (kind, patternId) => {
    const store = useGradingStore()
    const resolveSpy = vi.spyOn(store, 'resolveConflict').mockResolvedValue(undefined)
    const wrapper = mount(ConflictReviewPanel, { props: { conflicts: [conflict] } })

    await wrapper.get(`[data-testid="select-${kind}-pattern"]`).trigger('click')

    expect(resolveSpy).toHaveBeenCalledWith('conflict-1', patternId, '')
  })
})
