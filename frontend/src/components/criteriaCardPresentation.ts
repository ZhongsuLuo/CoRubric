import type {
  AlignmentConflict,
  AlignmentReadiness,
  AutoGradeAbstention,
  AutoGradeJobStatusItem,
  CriteriaMode,
  CriterionAuthorization,
  CriterionConsistencyState,
  CriterionProposalItem,
  GradingItem,
  ReviewAssignment,
} from '../types/schema'
import { translate as t } from '../i18n'

export type CriteriaCardNotice = {
  label: string
  className: string
  testId?: string
}

export type CriteriaCardStatusTag = CriteriaCardNotice & {
  key: string
  title?: string
  spinner?: boolean
}

export const isAutoGradeMiss = (input: {
  grading?: GradingItem | null
  abstention?: AutoGradeAbstention | null
  autoGradeJob?: AutoGradeJobStatusItem | null
}) => {
  if (input.grading) return false
  if (input.abstention) return true
  return ['done', 'abstained', 'failed', 'stale'].includes(String(input.autoGradeJob?.status || ''))
}

export const authorizationState = (authorization?: CriterionAuthorization | null) =>
  authorization?.state || 'teacher_validation'

export const lifecycleLabel = (state?: string | null) => {
  const labels: Record<string, string> = {
    active_selective: 'Active selective',
    evaluating: 'Evaluating',
    suspended: 'Suspended',
    collecting: 'Collecting',
  }
  return t((state && labels[state]) || state || 'Collecting')
}

export const lifecycleClass = (state?: string | null) => {
  if (state === 'active_selective') return 'status-badge-verified'
  if (state === 'evaluating') return 'status-badge-running'
  if (state === 'suspended') return 'status-badge-review'
  return 'status-badge-neutral'
}

export const autoGradeJobLabel = (status?: string | null) => {
  const labels: Record<string, string> = {
    pending: 'Queued',
    running: 'Auto-grading',
    done: 'Auto-graded',
    abstained: 'Abstained',
    failed: 'Failed',
    stale: 'Stale',
  }
  return t((status && labels[status]) || status || '')
}

export const autoGradeJobClass = (status?: string | null) => {
  const classes: Record<string, string> = {
    pending: 'status-badge-running',
    running: 'status-badge-running',
    done: 'status-badge-verified',
    abstained: 'status-badge-review',
    failed: 'status-badge-conflict',
    stale: 'status-badge-review',
  }
  return (status && classes[status]) || 'status-badge-neutral'
}

export const reviewJobVisible = (job?: AutoGradeJobStatusItem | null) => {
  if (!job?.status) return false
  if (job.status === 'pending' || job.status === 'running') return true
  if (job.status === 'done') return Boolean(job.diagnosis || job.reason)
  return job.status === 'failed'
}

export const reviewJobLabel = (job?: AutoGradeJobStatusItem | null) => {
  if (job?.status === 'pending') return t('Review queued')
  if (job?.status === 'running') return t('Reviewing standard')
  if (job?.status === 'failed') return t('Review failed')
  if (job?.status !== 'done') return job?.status || ''
  if (job.diagnosis === 'consistent') return t('Review clean')
  if (job.diagnosis === 'inconclusive') return t('Needs confirmation')
  return job.diagnosis ? job.diagnosis.replace(/_/g, ' ') : t('Review done')
}

export const reviewJobClass = (job?: AutoGradeJobStatusItem | null) => {
  if (job?.status === 'pending' || job?.status === 'running') {
    return 'status-badge-running'
  }
  if (job?.status === 'done' && job.diagnosis === 'consistent') {
    return 'status-badge-verified'
  }
  if (job?.status === 'done' && job.diagnosis === 'inconclusive') {
    return 'status-badge-review'
  }
  if (job?.status === 'failed') return 'status-badge-conflict'
  return 'status-badge-neutral'
}

export const pendingProposalLabel = (proposals?: CriterionProposalItem[]) => {
  const count = proposals?.length ?? 0
  if (count > 1) return t('{count} update proposals', { count })
  return proposals?.[0]?.proposal_type === 'create_criterion'
    ? 'Create proposal'
    : 'Update proposal'
}

export const conflictBadgeLabel = (conflicts?: AlignmentConflict[]) => {
  const count = conflicts?.length ?? 0
  if (count > 1) return t('{count} conflicts', { count })
  const conflict = conflicts?.[0]
  return conflict?.diagnosis ? conflict.diagnosis.replace(/_/g, ' ') : 'Conflict'
}

export const consistencyLabel = (state?: string | null) => {
  if (state === 'reliable') return t('Reliable consistency')
  if (state === 'unreliable') return t('Review needed')
  return t('Calibrating')
}

export const consistencyClass = (state?: string | null) => {
  if (state === 'reliable') return 'status-badge-verified'
  if (state === 'unreliable') return 'status-badge-review'
  return 'status-badge-running'
}

export const consistencyPercent = (consistency?: CriterionConsistencyState | null) =>
  (consistency?.agreement_count ?? 0) + (consistency?.correction_count ?? 0) === 0
    ? t('Insufficient samples')
    : `${Math.round((consistency?.smoothed_agreement ?? 0.5) * 100)}%`

type NoticeInput = {
  authorization?: CriterionAuthorization | null
  conflicts?: AlignmentConflict[]
  abstention?: AutoGradeAbstention | null
  autoGradeJob?: AutoGradeJobStatusItem | null
  auditJob?: AutoGradeJobStatusItem | null
  pendingProposals?: CriterionProposalItem[]
  readiness?: AlignmentReadiness | null
}

export const criteriaCardNotice = (input: NoticeInput): CriteriaCardNotice | null => {
  if (input.conflicts?.length) {
    return {
      label:
        authorizationState(input.authorization) === 'batch_authorized'
          ? t('Automation paused by conflict')
          : conflictBadgeLabel(input.conflicts),
      className: 'status-badge-conflict',
    }
  }
  if (input.abstention) {
    return {
      label: t('Abstained · teacher review needed'),
      className: 'status-badge-review',
    }
  }
  if (input.autoGradeJob && input.autoGradeJob.status !== 'done') {
    return {
      label: autoGradeJobLabel(input.autoGradeJob.status),
      className: autoGradeJobClass(input.autoGradeJob.status),
    }
  }
  if (
    reviewJobVisible(input.auditJob) &&
    !(input.auditJob?.status === 'done' && input.auditJob.diagnosis === 'consistent')
  ) {
    return {
      label: reviewJobLabel(input.auditJob),
      className: reviewJobClass(input.auditJob),
    }
  }
  if (input.pendingProposals?.length) {
    return {
      label: pendingProposalLabel(input.pendingProposals),
      className: 'status-badge-review',
    }
  }
  if (authorizationState(input.authorization) === 'repair_required') {
    return {
      label: t('Authorization paused'),
      className: 'status-badge-review',
    }
  }
  if (input.readiness?.lifecycle_state === 'suspended') {
    return { label: t('Suspended'), className: 'status-badge-review' }
  }
  return null
}

type StatusTagInput = NoticeInput & {
  criterionId: string
  mode: CriteriaMode
  consistency?: CriterionConsistencyState | null
  reviewAssignment?: ReviewAssignment | null
  autoGradeMissed?: boolean
}

const reviewRequired = (assignment?: ReviewAssignment | null) =>
  Boolean(assignment?.review_required && !assignment.reviewed_at)

const globalStatusTags = (input: StatusTagInput): CriteriaCardStatusTag[] => {
  const tags: CriteriaCardStatusTag[] = [
    {
      key: 'lifecycle',
      label: lifecycleLabel(input.readiness?.lifecycle_state),
      className: lifecycleClass(input.readiness?.lifecycle_state),
    },
  ]

  if (input.consistency) {
    tags.push({
      key: 'consistency',
      label: `${consistencyLabel(input.consistency.state)} · ${consistencyPercent(input.consistency)}`,
      className: consistencyClass(input.consistency.state),
      testId: `consistency-badge-${input.criterionId}`,
    })
  }
  if (reviewRequired(input.reviewAssignment)) {
    tags.push({
      key: 'review-required',
      label: t('Calibration review required'),
      className: 'status-badge-review',
      testId: `review-required-${input.criterionId}`,
    })
  }
  if (input.conflicts?.length) {
    tags.push({
      key: 'conflict',
      label: conflictBadgeLabel(input.conflicts),
      className: 'status-badge-conflict',
    })
  }
  if (input.abstention) {
    tags.push({
      key: 'abstention',
      label: t('Abstained'),
      className: 'status-badge-review',
    })
  }
  if (input.autoGradeJob) {
    tags.push({
      key: 'auto-grade-job',
      label: autoGradeJobLabel(input.autoGradeJob.status),
      className: autoGradeJobClass(input.autoGradeJob.status),
    })
  }
  if (reviewJobVisible(input.auditJob)) {
    tags.push({
      key: 'audit-job',
      label: reviewJobLabel(input.auditJob),
      className: reviewJobClass(input.auditJob),
      spinner: input.auditJob?.status === 'pending' || input.auditJob?.status === 'running',
    })
  }
  if (input.pendingProposals?.length) {
    tags.push({
      key: 'proposal',
      label: pendingProposalLabel(input.pendingProposals),
      className: 'status-badge-review',
    })
  }

  const authorization = authorizationState(input.authorization)
  if (authorization === 'batch_authorized') {
    tags.push({
      key: 'authorization',
      label: input.conflicts?.length ? t('Automation paused by conflict') : t('Model authorized'),
      className: input.conflicts?.length ? 'status-badge-conflict' : 'status-badge-verified',
    })
  } else if (authorization === 'repair_required') {
    tags.push({
      key: 'authorization',
      label: t('Authorization paused'),
      className: 'status-badge-review',
    })
  }
  return tags
}

const singleReportStatusTags = (input: StatusTagInput): CriteriaCardStatusTag[] => {
  const tags: CriteriaCardStatusTag[] = []
  const notice = criteriaCardNotice(input)
  if (notice) tags.push({ key: 'notice', ...notice })

  if (input.autoGradeMissed) {
    tags.push({
      key: 'not-auto-graded',
      label: t('Not auto-graded'),
      className: 'status-badge-review',
      testId: `not-auto-graded-${input.criterionId}`,
    })
  }
  if (reviewRequired(input.reviewAssignment)) {
    tags.push({
      key: 'review-required',
      label: t('Need review'),
      className: 'status-badge-review',
      testId: `review-required-${input.criterionId}`,
      title: t('Selected by the {percent}% consistency review sample', {
        percent: Math.round((input.reviewAssignment?.review_rate ?? 0) * 100),
      }),
    })
  }
  return tags
}

export const criteriaCardStatusTags = (input: StatusTagInput): CriteriaCardStatusTag[] =>
  input.mode === 'global' ? globalStatusTags(input) : singleReportStatusTags(input)
