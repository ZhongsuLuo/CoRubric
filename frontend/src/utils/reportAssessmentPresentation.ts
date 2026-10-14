import type {
  CriteriaItem,
  EvidenceAnchor,
  GradingItem,
  HighlightItem,
  ScoringPoint,
} from '../types/schema'
import { scoringPointDeductionCapacity } from './scoringPoints'

export type AssessmentPoint = {
  point: ScoringPoint
  score: number | null
  maxScore: number
  matched: boolean
}

export const evidenceColorClass = (color: HighlightItem['color']) => {
  if (color === 'green') return 'evidence-marker-model'
  if (color === 'red') return 'evidence-marker-conflict'
  if (color === 'amber') return 'evidence-marker-review'
  if (color === 'purple') return 'evidence-marker-pending'
  return 'evidence-marker-teacher'
}

export const evidenceStrokeColor = (color: HighlightItem['color']) => {
  if (color === 'green') return 'var(--source-model)'
  if (color === 'red') return 'var(--status-conflict)'
  if (color === 'amber') return 'var(--status-review)'
  if (color === 'purple') return 'var(--interaction-active)'
  return 'var(--source-teacher)'
}

export const formatAssessmentScore = (value: number) =>
  Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))

export const scorePercent = (score: number | null | undefined, maximum: number) => {
  const normalizedScore = Number(score)
  const normalizedMaximum = Number(maximum)
  if (!Number.isFinite(normalizedScore) || !Number.isFinite(normalizedMaximum)) return 0
  if (normalizedMaximum <= 0) return 0
  return Math.max(0, Math.min(100, (normalizedScore / normalizedMaximum) * 100))
}

const pointMaximum = (point: ScoringPoint, explicitMaximum?: number | null) => {
  const maximum = Number(explicitMaximum)
  if (Number.isFinite(maximum) && maximum >= 0) return maximum
  return Math.max(0, scoringPointDeductionCapacity(point))
}

export const assessmentPoints = (
  criterion: CriteriaItem,
  grading?: GradingItem | null,
): AssessmentPoint[] => {
  const scores = new Map(
    (grading?.scoring_point_scores || []).map((score) => [String(score.point_id), score]),
  )
  const matched = new Set((grading?.matched_scoring_points || []).map(String))

  return criterion.scoring_points.map((point) => {
    const pointScore = scores.get(String(point.point_id))
    const numericScore = Number(pointScore?.score)
    return {
      point,
      score: Number.isFinite(numericScore) ? numericScore : null,
      maxScore: pointMaximum(point, pointScore?.max_score),
      matched: matched.has(String(point.point_id)),
    }
  })
}

export const evidenceAnchorLabel = (anchor: EvidenceAnchor) => {
  if (anchor.anchor_type === 'image') {
    return anchor.image_alt || anchor.description || anchor.image_src || ''
  }
  if (anchor.anchor_type === 'absence') {
    return anchor.missing_item || anchor.expected_location || anchor.description || ''
  }
  return anchor.text || anchor.description || ''
}

export const evidenceSegmentLabel = (segment: HighlightItem) =>
  evidenceAnchorLabel(segment.anchor).replace(/\s+/g, ' ').trim()

export const graderType = (grading?: GradingItem | null) => {
  const provenance = grading?.provenance
  const provenanceType =
    provenance && typeof provenance === 'object' ? String(provenance.grader_type || '') : ''
  return (provenanceType || grading?.grader_type || grading?.graded_by || '').toLowerCase()
}
