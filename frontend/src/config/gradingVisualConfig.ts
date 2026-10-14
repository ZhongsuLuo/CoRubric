export const GLOBAL_DISTRIBUTION_REPORT_LIMIT = 50

export const gradingVisualConfig = {
  teacher: {
    label: 'Teacher',
    dotFill: 'var(--source-teacher)',
    highlightColor: 'blue',
    textClass: 'text-[var(--source-teacher)]',
    badgeClass: 'source-badge-teacher',
  },
  model: {
    label: 'Model',
    dotFill: 'var(--source-model)',
    highlightColor: 'green',
    textClass: 'text-[var(--source-model)]',
    badgeClass: 'source-badge-model',
  },
  unknown: {
    label: 'Unknown',
    dotFill: 'var(--source-unknown)',
    highlightColor: 'blue',
    textClass: 'text-[var(--source-unknown)]',
    badgeClass: 'source-badge-unknown',
  },
  conflict: {
    label: 'Conflict',
    ringStroke: 'var(--status-conflict)',
    highlightColor: 'red',
    textClass: 'text-[var(--status-conflict)]',
    badgeClass: 'status-badge-conflict',
  },
} as const

// Matrix-only warm sequential palette. These fills are not assigned to
// workflow status elsewhere; depth communicates normalized loss magnitude.
export const gradingMatrixVisualConfig = {
  full: 'heatmap-cell-loss-none',
  lowLoss: 'heatmap-cell-loss-low',
  mediumLoss: 'heatmap-cell-loss-medium',
  highLoss: 'heatmap-cell-loss-high',
  severeLoss: 'heatmap-cell-loss-severe',
  ungraded: 'heatmap-cell-ungraded border-slate-300 bg-white',
  queued: 'heatmap-cell-queued border-slate-200 bg-slate-100',
  running: 'heatmap-cell-running border-[#3f6f96] bg-[#edf3f7]',
  failed: 'heatmap-cell-failed border-slate-800 bg-slate-700',
  conflict: 'heatmap-cell-conflict border-[#a33d46] bg-white',
} as const

export type GradingAuthor = keyof Pick<typeof gradingVisualConfig, 'teacher' | 'model' | 'unknown'>
