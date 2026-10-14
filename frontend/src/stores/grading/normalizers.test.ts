import { describe, expect, it } from 'vitest'
import { normalizeAlignment } from './normalizers'

const pattern = (id: string) => ({
  pattern_id: `pattern-${id}`,
  event_ids: [id],
  representative_event_id: id,
  event_id: id,
  grader_type: 'teacher',
  criteria_score: id === 'teacher-a' ? 2 : 1,
})

describe('alignment conflict normalization', () => {
  it('keeps only conflicts backed by two complete teacher patterns', () => {
    const valid = {
      conflict_id: 'valid',
      conflict_type: 'teacher_teacher',
      criteria_id: '1',
      teacher_event_ids: ['teacher-a', 'teacher-b'],
      current_pattern: pattern('teacher-a'),
      alternative_pattern: pattern('teacher-b'),
    }
    const state = normalizeAlignment({
      open_conflicts: [
        valid,
        { ...valid, conflict_id: 'legacy', conflict_type: 'score_rubric' },
        {
          ...valid,
          conflict_id: 'model',
          alternative_pattern: { ...pattern('teacher-b'), grader_type: 'model' },
        },
        { ...valid, conflict_id: 'missing-side', alternative_pattern: null },
      ],
    })

    expect(state.open_conflicts.map((item) => item.conflict_id)).toEqual(['valid'])
  })
})
