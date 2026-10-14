import type { GlobalScorePoint } from '../types/schema'

export interface PositionedScorePoint extends GlobalScorePoint {
  x: number
  y: number
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

export const layoutGlobalScorePoints = (
  points: GlobalScorePoint[],
  maxScore: number,
  width = 240,
  height = 88,
): PositionedScorePoint[] => {
  const safeMax = Number.isFinite(maxScore) && maxScore > 0 ? maxScore : 1
  const left = 10
  const right = width - 10
  const bottom = height - 20
  const xForScore = (score: number) => left + (clamp(score, 0, safeMax) / safeMax) * (right - left)
  const groups = new Map<number, GlobalScorePoint[]>()

  points
    .filter((point) => Number.isFinite(Number(point.score)))
    .sort((a, b) => Number(a.score) - Number(b.score) || a.report_name.localeCompare(b.report_name))
    .forEach((point) => {
      const bin = Math.round(xForScore(Number(point.score)) / 10)
      const group = groups.get(bin) || []
      group.push(point)
      groups.set(bin, group)
    })

  const positioned: PositionedScorePoint[] = []
  groups.forEach((group) => {
    const center =
      group.reduce((total, point) => total + xForScore(Number(point.score)), 0) / group.length
    group.forEach((point, index) => {
      const row = index % 8
      const column = Math.floor(index / 8)
      const direction = column === 0 ? 0 : column % 2 === 1 ? 1 : -1
      const distance = Math.ceil(column / 2) * 8
      positioned.push({
        ...point,
        x: clamp(center + direction * distance, left, right),
        y: bottom - row * 7,
      })
    })
  })
  return positioned
}
