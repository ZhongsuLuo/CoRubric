import { describe, expect, it } from 'vitest'
import type { CriteriaItem, GradingItem, HighlightItem } from '../types/schema'
import {
  assessmentPoints,
  evidenceColorClass,
  evidenceSegmentLabel,
  evidenceStrokeColor,
  scorePercent,
} from './reportAssessmentPresentation'

const criterion = (id: string, name: string, maximum: number): CriteriaItem => ({
  criteria_id: id,
  criteria_name: name,
  description: name,
  original_description: name,
  max_score: maximum,
  few_shot_examples: [],
  scoring_points: [
    {
      point_id: `${id}.p1`,
      summary: 'Primary requirement',
      max_deduction: maximum,
      deduction_rules: [{ rule_id: `${id}.p1.r1`, condition: 'Missing', deduct: maximum }],
    },
  ],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
})

const grading = (item: CriteriaItem, score: number): GradingItem => ({
  ...item,
  criteria_score: score,
  file_original_content: '',
  section: '',
  target_section: null,
  score_reason: '',
  matched_scoring_points: [`${item.criteria_id}.p1`],
  scoring_point_scores: [{ point_id: `${item.criteria_id}.p1`, score, max_score: item.max_score }],
})

describe('report assessment presentation', () => {
  it('keeps evidence marker and connection colors semantically aligned', () => {
    expect(evidenceColorClass('green')).toBe('evidence-marker-model')
    expect(evidenceStrokeColor('green')).toBe('var(--source-model)')
    expect(evidenceColorClass('blue')).toBe('evidence-marker-teacher')
    expect(evidenceStrokeColor('blue')).toBe('var(--source-teacher)')
    expect(evidenceColorClass('purple')).toBe('evidence-marker-pending')
    expect(evidenceStrokeColor('purple')).toBe('var(--interaction-active)')
  })

  it('normalizes score ratios and clamps invalid ranges', () => {
    expect(scorePercent(3, 5)).toBe(60)
    expect(scorePercent(8, 5)).toBe(100)
    expect(scorePercent(-2, 5)).toBe(0)
    expect(scorePercent(2, 0)).toBe(0)
    expect(scorePercent(null, 5)).toBe(0)
  })

  it('keeps explicit scoring-point scores separate from match state', () => {
    const item = criterion('c1', 'Evidence', 4)
    const rows = assessmentPoints(item, grading(item, 3))

    expect(rows).toEqual([expect.objectContaining({ score: 3, maxScore: 4, matched: true })])
  })

  it('formats text, image, and absence evidence without inventing locations', () => {
    const base: HighlightItem = {
      id: 'e1',
      report_id: 'r1',
      criteria_id: 'c1',
      score: 1,
      reason: '',
      source: 'llm',
      color: 'blue',
      anchor: { text: '  Exact\n evidence  ' },
    }

    expect(evidenceSegmentLabel(base)).toBe('Exact evidence')
    expect(
      evidenceSegmentLabel({
        ...base,
        anchor: {
          anchor_type: 'image',
          text: '',
          image_alt: 'Calibration chart',
        },
      }),
    ).toBe('Calibration chart')
    expect(
      evidenceSegmentLabel({
        ...base,
        anchor: {
          anchor_type: 'absence',
          text: '',
          missing_item: 'Safety statement',
        },
      }),
    ).toBe('Safety statement')
  })
})
