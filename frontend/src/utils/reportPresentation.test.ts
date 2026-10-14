import { describe, expect, it } from 'vitest'
import type { AutoGradeBatch, AutoGradeJobStatus } from '../types/schema'
import {
  findTargetSection,
  formatScore,
  getReportAutoGradeLabel,
  getReportAutoGradeProgress,
  getReportAutoGradeTone,
} from './reportPresentation'

const batchWith = (...statuses: AutoGradeJobStatus[]): AutoGradeBatch => ({
  batch_id: 'batch-1',
  case_id: 'case-1',
  criteria_file_name: 'rubric.json',
  rubric_version: 'v1',
  model_id: 'model-1',
  status: 'running',
  created_at: '2026-08-23T00:00:00Z',
  updated_at: '2026-08-23T00:00:00Z',
  report_names: ['report-a'],
  criteria: [],
  targets: statuses.map((status, index) => ({
    report_name: 'report-a',
    criterion_id: String(index + 1),
    status,
  })),
  progress: {
    total: statuses.length,
    completed: 0,
    percent: 0,
    pending: 0,
    running: 0,
    done: 0,
    abstained: 0,
    failed: 0,
    stale: 0,
  },
  calibration: { required: false, status: 'confirmed', rubric_version: 'v1' },
})

describe('report presentation', () => {
  it('finds the nearest preceding markdown heading with flexible whitespace', () => {
    const markdown = '# Intro\nFirst text\n\n## Analysis\nThe result is  42 mg/L.\n\n## End\nDone.'

    expect(findTargetSection(markdown, 'result is 42 mg/L')).toBe('Analysis')
    expect(findTargetSection(markdown, 'missing')).toBeNull()
    expect(findTargetSection('', 'result')).toBeNull()
  })

  it('summarizes running, completed, and issue states for one report', () => {
    const running = getReportAutoGradeProgress(batchWith('done', 'running', 'queued'), 'report-a')
    expect(running).toMatchObject({ total: 3, done: 1, running: 1, terminal: 1, complete: false })
    expect(getReportAutoGradeLabel(running)).toBe('Auto-grading · 1/3')
    expect(getReportAutoGradeTone(running)).toBe('running')

    const complete = getReportAutoGradeProgress(batchWith('done', 'done'), 'report-a')
    expect(getReportAutoGradeLabel(complete)).toBe('Auto-grading complete · 2/2')
    expect(getReportAutoGradeTone(complete)).toBe('complete')

    const issues = getReportAutoGradeProgress(batchWith('done', 'abstained', 'stale'), 'report-a')
    expect(getReportAutoGradeLabel(issues)).toBe(
      'Auto-grading finished · 1/3 · 1 review · 1 failed',
    )
    expect(getReportAutoGradeTone(issues)).toBe('issues')
  })

  it('handles absent reports, queued work, and compact score formatting', () => {
    expect(getReportAutoGradeProgress(null, 'report-a')).toBeNull()
    expect(getReportAutoGradeProgress(batchWith('queued'), 'report-b')).toBeNull()

    const queued = getReportAutoGradeProgress(batchWith('queued'), 'report-a')
    expect(getReportAutoGradeLabel(queued)).toBe('Waiting for auto-grading · 0/1')
    expect(getReportAutoGradeTone(queued)).toBe('queued')
    expect(formatScore(8)).toBe('8')
    expect(formatScore(8.126)).toBe('8.13')
  })
})
