import type { AutoGradeBatch } from '../types/schema'

export interface ReportAutoGradeProgress {
  total: number
  done: number
  abstained: number
  failed: number
  running: number
  terminal: number
  complete: boolean
}

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

export const findTargetSection = (markdown: string, snippet: string): string | null => {
  if (!markdown || !snippet) return null
  const match = new RegExp(escapeRegExp(snippet.trim()).replace(/\s+/g, '\\s+'), 'i').exec(markdown)
  if (!match) return null

  let section: string | null = null
  for (const heading of markdown.matchAll(/^#{1,6}\s+.+$/gm)) {
    if (heading.index === undefined || heading.index >= match.index) break
    section = heading[0].replace(/^#{1,6}\s+/, '').trim()
  }
  return section
}

export const getReportAutoGradeProgress = (
  batch: AutoGradeBatch | null,
  reportId: string,
): ReportAutoGradeProgress | null => {
  if (!batch) return null
  const targets = batch.targets.filter((target) => target.report_name === reportId)
  if (!targets.length) return null

  const count = (statuses: string[]) =>
    targets.filter((target) => statuses.includes(target.status)).length
  const done = count(['done'])
  const abstained = count(['abstained'])
  const failed = count(['failed', 'stale'])
  const running = count(['running'])
  const terminal = done + abstained + failed
  return {
    total: targets.length,
    done,
    abstained,
    failed,
    running,
    terminal,
    complete: terminal === targets.length,
  }
}

export const getReportAutoGradeLabel = (progress: ReportAutoGradeProgress | null) => {
  if (!progress) return ''
  const count = `${progress.done}/${progress.total}`
  if (progress.complete && !progress.failed && !progress.abstained) {
    return `Auto-grading complete · ${count}`
  }
  if (progress.complete) {
    const issues = [
      progress.abstained ? `${progress.abstained} review` : '',
      progress.failed ? `${progress.failed} failed` : '',
    ].filter(Boolean)
    return `Auto-grading finished · ${count} · ${issues.join(' · ')}`
  }
  if (progress.running || progress.terminal) return `Auto-grading · ${count}`
  return `Waiting for auto-grading · ${count}`
}

export const getReportAutoGradeTone = (progress: ReportAutoGradeProgress | null) => {
  if (!progress) return 'idle'
  if (!progress.complete) return progress.running || progress.terminal ? 'running' : 'queued'
  return progress.failed || progress.abstained ? 'issues' : 'complete'
}

export const formatScore = (value: number) =>
  Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
