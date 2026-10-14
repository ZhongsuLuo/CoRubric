import type { AutoGradeBatchStatus, AutoGradeJobStatus } from '../types/schema'
import { translate as t } from '../i18n'

export const TERMINAL_AUTO_GRADE_BATCH_STATUSES = new Set<AutoGradeBatchStatus>([
  'completed',
  'completed_with_issues',
  'failed',
  'superseded',
  'cancelled',
])

export const TERMINAL_AUTO_GRADE_TARGET_STATUSES = new Set<AutoGradeJobStatus>([
  'done',
  'abstained',
  'failed',
  'stale',
  'cancelled',
])

export const isTerminalAutoGradeBatch = (status: AutoGradeBatchStatus) =>
  TERMINAL_AUTO_GRADE_BATCH_STATUSES.has(status)

export const shouldPollAutoGradeBatch = (status: AutoGradeBatchStatus) =>
  status !== 'awaiting_calibration' && !isTerminalAutoGradeBatch(status)

export const autoGradeBatchStatusLabel = (status: AutoGradeBatchStatus) => {
  if (status === 'awaiting_calibration') return t('Ready to start')
  if (status === 'queued') return t('Queued')
  if (status === 'running') return t('Auto-grading')
  if (status === 'cancelling') return t('Stopping after current report')
  if (status === 'cancelled') return t('Stopped')
  if (status === 'completed_with_issues') return t('Completed · review needed')
  if (status === 'completed') return t('Completed')
  if (status === 'failed') return t('Failed')
  if (status === 'superseded') return t('Replaced by rubric update')
  return t(status)
}
