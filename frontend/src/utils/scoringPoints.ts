import type {
  DeductionRule,
  ScoringAggregation,
  ScoringPoint,
  ScoringPointScore,
} from '../types/schema'
import { translate as t } from '../i18n'

export type StructuredChangeKind = 'added' | 'removed' | 'modified'

export interface StructuredFieldChange {
  field: string
  label: string
  before: unknown
  after: unknown
}

export interface ScoringPointChange {
  kind: StructuredChangeKind
  pointId: string
  before?: ScoringPoint
  after?: ScoringPoint
  fields: StructuredFieldChange[]
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

export const cloneScoringPoints = (points?: ScoringPoint[] | null): ScoringPoint[] =>
  clone(points || []).map((point) => ({
    ...point,
    max_deduction: (point.max_deduction as unknown) === '' ? null : point.max_deduction,
    deduction_rules: point.deduction_rules || [],
  }))

export const normalizeScoringAggregation = (
  aggregation: ScoringAggregation,
): ScoringAggregation => {
  const normalized = clone(aggregation)
  if ((normalized.cap as unknown) === '') normalized.cap = null
  return normalized
}

export const diffScoringPoints = (
  before?: ScoringPoint[] | null,
  after?: ScoringPoint[] | null,
): ScoringPointChange[] => {
  const beforeById = new Map((before || []).map((point) => [point.point_id, point]))
  const afterById = new Map((after || []).map((point) => [point.point_id, point]))
  const ids = [...beforeById.keys(), ...afterById.keys()]
  const seen = new Set<string>()

  const changes: ScoringPointChange[] = []
  for (const pointId of ids) {
    if (seen.has(pointId)) continue
    seen.add(pointId)
    const previous = beforeById.get(pointId)
    const next = afterById.get(pointId)
    if (!previous && next) {
      changes.push({ kind: 'added', pointId, after: next, fields: [] })
      continue
    }
    if (previous && !next) {
      changes.push({ kind: 'removed', pointId, before: previous, fields: [] })
      continue
    }
    if (!previous || !next) continue

    const fields: StructuredFieldChange[] = []
    if ((previous.summary ?? null) !== (next.summary ?? null)) {
      fields.push({
        field: 'summary',
        label: 'Scoring point content',
        before: previous.summary,
        after: next.summary,
      })
    }
    const previousScore = scoringPointDeductionCapacity(previous)
    const nextScore = scoringPointDeductionCapacity(next)
    if (previousScore !== nextScore) {
      fields.push({
        field: 'score',
        label: 'Score',
        before: previousScore,
        after: nextScore,
      })
    }
    if (fields.length)
      changes.push({ kind: 'modified', pointId, before: previous, after: next, fields })
  }
  return changes
}

export const structuredChangeSummary = (
  before?: ScoringPoint[] | null,
  after?: ScoringPoint[] | null,
) => {
  const changes = diffScoringPoints(before, after)
  const added = changes.filter((change) => change.kind === 'added').length
  const removed = changes.filter((change) => change.kind === 'removed').length
  const modified = changes.filter((change) => change.kind === 'modified').length
  const parts = [
    added && t('Added {count}', { count: added }),
    removed && t('Removed {count}', { count: removed }),
    modified && t('Updated {count}', { count: modified }),
  ].filter(Boolean)
  return parts.length
    ? t('Scoring points: {parts}', { parts: parts.join(' · ') })
    : t('No scoring point changes')
}

export const validateScoringPoints = (points?: ScoringPoint[] | null): string[] => {
  const errors: string[] = []
  if (!points?.length) return [t('At least one structured scoring point is required')]
  const pointIds = new Set<string>()
  const ruleIds = new Set<string>()
  ;(points || []).forEach((point, index) => {
    const pointIndex = index + 1
    let hasInvalidSavedData = false
    if (!point.point_id?.trim() || pointIds.has(point.point_id)) hasInvalidSavedData = true
    else pointIds.add(point.point_id)
    if (!point.summary?.trim())
      errors.push(t('Scoring point {index} is missing content', { index: pointIndex }))
    if (!point.deduction_rules?.length) hasInvalidSavedData = true
    if (
      point.max_deduction !== null &&
      point.max_deduction !== undefined &&
      (typeof point.max_deduction !== 'number' ||
        !Number.isFinite(point.max_deduction) ||
        point.max_deduction < 0)
    ) {
      errors.push(t('Scoring point {index} score must be non-negative', { index: pointIndex }))
    }
    ;(point.deduction_rules || []).forEach((rule) => {
      if (!rule.rule_id?.trim() || ruleIds.has(rule.rule_id)) hasInvalidSavedData = true
      else ruleIds.add(rule.rule_id)
      if (
        !rule.condition?.trim() ||
        typeof rule.deduct !== 'number' ||
        !Number.isFinite(rule.deduct) ||
        rule.deduct < 0
      ) {
        hasInvalidSavedData = true
      }
    })
    if (hasInvalidSavedData) {
      errors.push(
        t('Scoring point {index} contains invalid saved data. Remove it and add it again.', {
          index: pointIndex,
        }),
      )
    }
  })
  return errors
}

export const validateScoringAggregation = (aggregation?: ScoringAggregation | null): string[] => {
  if (!aggregation) return []
  if (!aggregation.mode?.trim()) return [t('Scoring settings could not be saved')]
  if (
    aggregation.cap !== null &&
    aggregation.cap !== undefined &&
    (typeof aggregation.cap !== 'number' ||
      !Number.isFinite(aggregation.cap) ||
      aggregation.cap < 0)
  ) {
    return [t('Scoring settings could not be saved')]
  }
  return []
}

export const scoringPointDeductionCapacity = (point: ScoringPoint): number => {
  const deductions = (point.deduction_rules || []).map((rule) => Number(rule.deduct) || 0)
  let capacity =
    point.aggregation === 'max_triggered'
      ? Math.max(0, ...deductions)
      : deductions.reduce((sum, value) => sum + value, 0)
  if (point.max_deduction !== null && point.max_deduction !== undefined) {
    capacity = Math.min(capacity, Number(point.max_deduction) || 0)
  }
  return Math.max(capacity, 0)
}

export const structuredDeductionCapacity = (
  points?: ScoringPoint[] | null,
  aggregation?: ScoringAggregation | null,
): number => {
  const capacities = (points || []).map(scoringPointDeductionCapacity)
  let capacity =
    aggregation?.mode === 'max_triggered'
      ? Math.max(0, ...capacities)
      : capacities.reduce((sum, value) => sum + value, 0)
  if (aggregation?.cap !== null && aggregation?.cap !== undefined) {
    capacity = Math.min(capacity, Number(aggregation.cap) || 0)
  }
  return Math.max(capacity, 0)
}

export const normalizeScoringPointScores = (
  points: ScoringPoint[] | null | undefined,
  scores?: ScoringPointScore[] | null,
  fallbackTotal?: number | null,
): ScoringPointScore[] => {
  const existing = new Map((scores || []).map((item) => [item.point_id, item]))
  const hasSavedScores = existing.size > 0
  let remaining = Number.isFinite(fallbackTotal ?? NaN)
    ? Math.max(Number(fallbackTotal), 0)
    : Number.POSITIVE_INFINITY
  return (points || []).map((point) => {
    const maxScore = scoringPointDeductionCapacity(point)
    const saved = existing.get(point.point_id)
    const value = Number(saved?.score)
    const fallbackScore = Number.isFinite(remaining) ? Math.min(maxScore, remaining) : maxScore
    if (Number.isFinite(remaining)) remaining = Math.max(remaining - fallbackScore, 0)
    return {
      point_id: point.point_id,
      score:
        hasSavedScores && Number.isFinite(value)
          ? Math.min(Math.max(value, 0), maxScore)
          : fallbackScore,
      max_score: maxScore,
      reason: saved?.reason ?? null,
    }
  })
}

export const structuredPointScoreTotal = (scores?: ScoringPointScore[] | null): number =>
  (scores || []).reduce((sum, item) => sum + (Number(item.score) || 0), 0)

export const validateStructuredScoreBudget = (
  points: ScoringPoint[] | null | undefined,
  maxScore: number,
  aggregation?: ScoringAggregation | null,
): string[] => {
  if (!points?.length) return []
  const capacity = structuredDeductionCapacity(points, aggregation)
  if (Math.abs(capacity - maxScore) < 1e-6) return []
  return [
    t('Scoring point scores total {total} points, but the criterion maximum is {maximum} points.', {
      total: capacity,
      maximum: maxScore,
    }),
  ]
}

export const newScoringPoint = (criterionId: string, index: number): ScoringPoint => ({
  point_id: `${criterionId || 'criterion'}.p${index + 1}`,
  summary: '',
  full_credit_condition: '',
  deduction_rules: [],
  max_deduction: null,
  aggregation: 'sum_deductions_capped',
  applies_when: '',
})

export const newDeductionRule = (pointId: string, index: number): DeductionRule => ({
  rule_id: `${pointId || 'point'}.r${index + 1}`,
  condition: '',
  deduct: 0,
  note: '',
})

export const updateScoringPointContent = (point: ScoringPoint, summary: string) => {
  point.summary = summary
  const onlyRule = point.deduction_rules.length === 1 ? point.deduction_rules[0] : null
  if (onlyRule) onlyRule.condition = summary
}

export const updateScoringPointScore = (point: ScoringPoint, rawScore: number | string) => {
  const score = Math.max(0, Number(rawScore) || 0)
  point.max_deduction = score
  point.deduction_rules ||= []

  if (!point.deduction_rules.length) {
    const rule = newDeductionRule(point.point_id, 0)
    rule.condition = point.summary
    rule.deduct = score
    point.deduction_rules.push(rule)
    return
  }

  const onlyRule = point.deduction_rules.length === 1 ? point.deduction_rules[0] : null
  if (onlyRule) {
    onlyRule.deduct = score
    if (!onlyRule.condition.trim()) onlyRule.condition = point.summary
    return
  }

  const primaryRule = point.deduction_rules[0]!
  const deductions = point.deduction_rules.map((rule) => Math.max(0, Number(rule.deduct) || 0))
  const internalCapacity =
    point.aggregation === 'max_triggered'
      ? Math.max(0, ...deductions)
      : deductions.reduce((sum, deduction) => sum + deduction, 0)
  if (internalCapacity >= score) return
  if (point.aggregation === 'max_triggered') primaryRule.deduct = score
  else primaryRule.deduct += score - internalCapacity
}

export const newUniqueScoringPoint = (points: ScoringPoint[], criterionId: string) => {
  const existingIds = new Set(points.map((point) => point.point_id))
  let index = points.length
  let point = newScoringPoint(criterionId, index)
  while (existingIds.has(point.point_id)) {
    index += 1
    point = newScoringPoint(criterionId, index)
  }
  updateScoringPointScore(point, 0)
  return point
}

export interface StructuredScoreCalculation {
  totalDeduction: number
  score: number
  matchedPointIds: string[]
}

export const calculateStructuredScore = (
  points: ScoringPoint[] | null | undefined,
  aggregation: ScoringAggregation | null | undefined,
  maxScore: number,
  matchedRuleIds: string[],
): StructuredScoreCalculation => {
  const selected = new Set(matchedRuleIds)
  const matchedPointIds: string[] = []
  const pointDeductions = (points || []).map((point) => {
    const triggered = (point.deduction_rules || []).filter((rule) => selected.has(rule.rule_id))
    if (!triggered.length) return 0
    matchedPointIds.push(point.point_id)
    const deductions = triggered.map((rule) => Number(rule.deduct) || 0)
    let deduction =
      point.aggregation === 'max_triggered'
        ? Math.max(...deductions)
        : deductions.reduce((sum, value) => sum + value, 0)
    if (point.max_deduction !== null && point.max_deduction !== undefined)
      deduction = Math.min(deduction, Number(point.max_deduction))
    return deduction
  })
  const mode = aggregation?.mode || 'sum_deductions_capped'
  let totalDeduction =
    mode === 'max_triggered'
      ? Math.max(0, ...pointDeductions)
      : pointDeductions.reduce((sum, value) => sum + value, 0)
  const cap = aggregation?.cap ?? maxScore
  totalDeduction = Math.min(
    Math.max(totalDeduction, 0),
    Math.max(Number(cap) || 0, 0),
    Math.max(maxScore, 0),
  )
  return {
    totalDeduction,
    score: Math.max(maxScore - totalDeduction, 0),
    matchedPointIds,
  }
}
