import type { CriteriaItem, ScoringPointScore } from '../../types/schema'

export type ReportAssessmentMode = 'score' | 'conflict' | 'rubric'

export interface ReportGradeDraft {
  criteriaId: string
  score: number
  reason: string
  anchorText: string
  matchedScoringPoints: string[]
  matchedDeductionRules: string[]
  scoringPointScores: ScoringPointScore[]
}

export interface ReportRubricDraft {
  criterion: CriteriaItem
}
