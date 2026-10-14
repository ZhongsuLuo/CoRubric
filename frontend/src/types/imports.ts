export type ImportStatus =
  | 'queued'
  | 'pending'
  | 'running'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'interrupted'
export interface CaseImportFile {
  id: string
  path: string
  report_name: string
  pages: number
  pages_done: number
  status: ImportStatus
  stage: string
  error: string | null
}
export interface CaseImportJob {
  id: string
  case_id: string
  engine: 'hybrid' | 'legacy'
  status: ImportStatus
  created_at: string
  updated_at: string
  files: CaseImportFile[]
  events: { time: string; message: string }[]
}
