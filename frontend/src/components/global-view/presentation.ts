import type { CriterionGlobalSummary } from '../../types/schema'
import { translate as t } from '../../i18n'

export type ReportTone = 'verified' | 'running' | 'review' | 'conflict' | 'neutral'

export const reportToneDotClass = (tone: ReportTone) => {
  if (tone === 'verified') return 'bg-[var(--status-verified)]'
  if (tone === 'conflict') return 'bg-[var(--status-conflict)]'
  if (tone === 'running') return 'bg-[var(--status-running)]'
  if (tone === 'review') return 'bg-[var(--status-review)]'
  return 'bg-[var(--status-neutral)]'
}

export const formatScore = (value: number | null | undefined) => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  return Number.isInteger(number)
    ? String(number)
    : number.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

export const formatLoss = (summary: CriterionGlobalSummary) =>
  summary.average_loss === null || summary.average_loss === undefined
    ? t('No scored reports')
    : t('{loss} avg. points lost', { loss: formatScore(summary.average_loss) })
