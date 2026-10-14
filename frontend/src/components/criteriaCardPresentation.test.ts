import { describe, expect, it } from 'vitest'
import {
  autoGradeJobClass,
  consistencyClass,
  lifecycleClass,
  reviewJobClass,
} from './criteriaCardPresentation'

type ReviewJob = NonNullable<Parameters<typeof reviewJobClass>[0]>

const reviewJob = (status: ReviewJob['status'], diagnosis?: ReviewJob['diagnosis']): ReviewJob => ({
  criteria_id: 'criterion-1',
  status,
  diagnosis,
})

describe('criteria card semantic status colors', () => {
  it('maps workflow states to the shared status tokens', () => {
    expect(autoGradeJobClass('pending')).toBe('status-badge-running')
    expect(autoGradeJobClass('running')).toBe('status-badge-running')
    expect(autoGradeJobClass('done')).toBe('status-badge-verified')
    expect(autoGradeJobClass('abstained')).toBe('status-badge-review')
    expect(autoGradeJobClass('failed')).toBe('status-badge-conflict')
    expect(autoGradeJobClass('stale')).toBe('status-badge-review')
  })

  it('keeps lifecycle, review and consistency meanings aligned', () => {
    expect(lifecycleClass('active_selective')).toBe('status-badge-verified')
    expect(lifecycleClass('evaluating')).toBe('status-badge-running')
    expect(lifecycleClass('suspended')).toBe('status-badge-review')
    expect(reviewJobClass(reviewJob('running'))).toBe('status-badge-running')
    expect(reviewJobClass(reviewJob('done', 'consistent'))).toBe('status-badge-verified')
    expect(reviewJobClass(reviewJob('done', 'inconclusive'))).toBe('status-badge-review')
    expect(reviewJobClass(reviewJob('failed'))).toBe('status-badge-conflict')
    expect(consistencyClass('reliable')).toBe('status-badge-verified')
    expect(consistencyClass('unreliable')).toBe('status-badge-review')
    expect(consistencyClass('calibrating')).toBe('status-badge-running')
  })
})
