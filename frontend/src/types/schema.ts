export interface FewShotExample {
  example_input: string
  example_output_description: string
  example_output_score: number
}

export interface DeductionRule {
  rule_id: string
  condition: string
  deduct: number
  note?: string | null
}

export interface ScoringPoint {
  point_id: string
  summary: string
  full_credit_condition?: string | null
  deduction_rules: DeductionRule[]
  max_deduction?: number | null
  aggregation?: string | null
  applies_when?: string | null
}

export interface ScoringPointScore {
  point_id: string
  score: number
  max_score?: number | null
  reason?: string | null
}

export interface ScoringAggregation {
  mode: string
  cap?: number | null
  score_formula?: string | null
}

export interface CriteriaItem {
  criteria_id: string
  criteria_name: string
  description: string
  original_description: string
  max_score: number
  section?: string | null
  few_shot_examples: FewShotExample[]
  scoring_points: ScoringPoint[]
  scoring_aggregation: ScoringAggregation
}

export interface CriteriaSchema {
  schema_version: 'structured-rubric-v1'
  title: string
  background: string
  language: string
  base_score: number
  criteria: CriteriaItem[]
}

export interface CriteriaCategoryAssignment {
  criteria_id: string
  criteria_name: string
  old_section?: string | null
  section: string
}

export interface CriteriaCategoryPreview {
  source_content_hash: string
  assignments: CriteriaCategoryAssignment[]
  categories: Array<{ section: string; count: number }>
  rationale: string
}

export type GraderType = 'teacher' | 'user' | 'model' | 'llm'

export interface GradingItem {
  criteria_id: string
  criteria_name?: string | null
  description: string
  original_description: string
  max_score: number
  criteria_score: number
  file_original_content: string
  section: string
  target_section: string | null
  score_reason: string
  anchor_text?: string | null
  evidence_anchors?: EvidenceAnchor[]
  graded_by?: GraderType | null
  grader_type?: GraderType | null
  acceptance_status?: string | null
  scoring_points: ScoringPoint[]
  scoring_aggregation: ScoringAggregation
  matched_scoring_points?: string[]
  matched_deduction_rules?: string[]
  scoring_point_scores?: ScoringPointScore[]
  rubric_file_name?: string | null
  provenance?: string | Record<string, unknown> | null
  lifecycle_state?: AlignmentLifecycle | null
}

export interface GradingResult {
  file_name: string
  language: string
  base_score: number
  file_score: number
  criteria: GradingItem[]
}

export type HighlightSource = 'teacher' | 'llm' | 'implicit'

export interface HighlightAnchor {
  anchor_type?: 'text' | 'image' | 'absence'
  text: string
  occurrence?: number
  start_offset?: number | null
  end_offset?: number | null
  image_src?: string | null
  image_alt?: string | null
  description?: string | null
  region?: Record<string, number> | null
  expected_location?: string | null
  missing_item?: string | null
}

export interface HighlightItem {
  id: string
  report_id: string
  criteria_id: string
  event_id?: string | null
  implicit_grade_id?: string | null
  proposal_id?: string | null
  score: number
  max_score?: number | null
  reason: string
  source: HighlightSource
  color: 'blue' | 'green' | 'red' | 'amber' | 'purple'
  anchor: HighlightAnchor
  status?: string | null
  graded_by?: GraderType | null
  provenance?: string | null
  lifecycle_state?: AlignmentLifecycle | null
}

export type CriteriaMode = 'single' | 'global'

export interface ReportItem {
  id: string
  name: string
  markdown: string
  conversion_status: 'converting' | 'finished'
}

export type AlignmentLifecycle =
  | 'collecting'
  | 'evaluating'
  | 'active_selective'
  | 'suspended'
  | string

export interface AlignmentReadiness {
  criteria_id: string
  lifecycle_state: AlignmentLifecycle
  lifecycle?: AlignmentLifecycle
  status?: AlignmentLifecycle
  state?: AlignmentLifecycle
  rubric_version?: string | null
  reason?: string | null
  matched_rule_ids?: string[]
  covered_rule_ids?: string[]
  allowed_evidence_types?: string[]
  allowed_sections?: string[]
  active?: boolean
  min_teacher_samples?: number
  readiness_policy_version?: string
}

export interface AlignmentConflict {
  conflict_id?: string | null
  conflict_type?: 'teacher_teacher' | 'score_rubric' | 'model_rubric_migration' | string | null
  criteria_id: string
  report_name?: string | null
  event_ids?: string[]
  suspect_event_ids?: string[]
  supporting_event_ids?: string[]
  affected_event_ids?: string[]
  suspect_events?: AlignmentConflictEventRef[]
  supporting_events?: AlignmentConflictEventRef[]
  affected_events?: AlignmentConflictEventRef[]
  teacher_event_ids?: string[]
  current_event_id?: string | null
  alternative_event_id?: string | null
  current_pattern?: TeacherScoringPattern | null
  alternative_pattern?: TeacherScoringPattern | null
  teacher_scores?: number[]
  diagnosis?: string | null
  confidence?: number | null
  reason?: string | null
  status?: string | null
  open?: boolean
  review_status?: string | null
  reassessment?: ConflictReassessment | null
}

export interface TeacherScoringPattern {
  pattern_id: string
  event_ids: string[]
  representative_event_id: string
  event_id: string
  report_name?: string | null
  grader_type?: 'teacher' | string | null
  criteria_score?: number | null
  score_reason?: string | null
  anchor_text?: string | null
  scoring_point_scores?: ScoringPointScore[]
  matched_scoring_points?: string[]
  matched_deduction_rules?: string[]
}

export interface ConflictReassessment {
  reassessment_id: string
  conflict_id: string
  event_id: string
  criterion_id: string
  rubric_version?: string | null
  diagnosis:
    | 'audit_error'
    | 'rubric_under_specified'
    | 'grade_or_evidence_issue'
    | 'model_grade_error'
    | 'inconclusive'
    | string
  reason?: string | null
  confidence?: number | null
  teacher_feedback?: string | null
  proposal_id?: string | null
}

export interface CriterionAuthorization {
  criteria_file_name: string
  criterion_id: string
  criteria_id?: string
  state: 'teacher_validation' | 'batch_authorized' | 'repair_required' | string
  authorized_rubric_version?: string | null
  repair_from_rubric_version?: string | null
  repair_to_rubric_version?: string | null
  repair_confirmed_at?: string | null
  repair_pending_report_names?: string[]
}

export interface GlobalScorePoint {
  report_id: string
  report_name: string
  criterion_id: string
  criteria_id?: string
  event_id?: string | null
  rubric_version?: string | null
  score: number
  max_score: number
  author: 'teacher' | 'model' | 'unknown'
  acceptance_status?: string | null
  conflict: boolean
  included_in_statistics?: boolean
  anchor_text?: string | null
  target_section?: string | null
  anchor_occurrence?: number | null
  evidence_anchors?: EvidenceAnchor[]
  score_reason?: string | null
  scoring_point_scores?: ScoringPointScore[]
  validity_status?: 'valid' | 'model_regrade_required' | 'teacher_repair_required' | string
  evidence_valid?: boolean
  allocation_valid?: boolean
}

export interface ScoringPointLossSummary {
  point_id: string
  summary: string
  average_loss?: number | null
  total_loss: number
  covered_report_count: number
  affected_report_count: number
  affected_report_ids: string[]
  refs: Array<{
    report_id: string
    report_name: string
    event_id?: string | null
    criterion_id: string
    point_id: string
    loss: number
    score_reason?: string | null
    evidence_anchors?: EvidenceAnchor[]
  }>
}

export interface CriterionGlobalSummary {
  criteria_file_name: string
  criterion_id: string
  criteria_id?: string
  rubric_version?: string | null
  max_score: number
  total_reports: number
  scored_count: number
  statistical_count?: number
  ungraded_count: number
  average_score?: number | null
  average_loss?: number | null
  total_loss?: number
  teacher_count: number
  model_count: number
  conflict_count: number
  migration_conflict_count: number
  eligible_count: number
  eligible_report_names: string[]
  retryable_count: number
  retryable_report_names: string[]
  job_counts: Record<string, number>
  point_loss_summaries?: ScoringPointLossSummary[]
  attribution_coverage_count?: number
  points: GlobalScorePoint[]
}

export interface GlobalReportSummary {
  report_id: string
  report_name: string
  analysis_summary: string
  score?: number | null
  base_score: number
  graded_criteria_count: number
  total_criteria_count: number
  conflict_count: number
  partial: boolean
  provisional: boolean
  teacher_count: number
  model_count: number
  criteria_scores: Record<
    string,
    {
      status: 'graded' | 'conflict' | 'not_graded' | string
      score?: number | null
      max_score?: number | null
      event_id?: string | null
      author?: 'teacher' | 'model' | 'unknown'
    }
  >
}

export interface GlobalReadinessBlocker {
  kind: string
  label: string
  report_name?: string | null
  criterion_id?: string | null
  event_id?: string | null
  implicit_grade_id?: string | null
  proposal_id?: string | null
  scoring_point_scores?: ScoringPointScore[]
}

export interface GlobalReadiness {
  allowed: boolean
  blocker_count: number
  blockers: GlobalReadinessBlocker[]
}

export interface CohortSynthesisInsight {
  insight_id: string
  title?: string
  summary: string
  criterion_ids: string[]
  point_ids: string[]
  report_ids: string[]
  supporting_refs: Array<Record<string, unknown>>
}

export interface CohortSynthesis {
  case_id: string
  criteria_file_name: string
  criterion_id?: string | null
  projection_hash: string
  model_id: string
  language?: string
  analysis?: string
  findings?: CohortSynthesisInsight[]
  insights: CohortSynthesisInsight[]
  generated_at: string
}

export interface AlignmentConflictEventRef {
  event_id: string
  report_name?: string | null
  criteria_id?: string | null
  criterion_id?: string | null
  rubric_file_name?: string | null
  grader_type?: string | null
  score?: number | null
  anchor_text?: string | null
  score_reason?: string | null
  /** Structured rubric items explicitly selected while grading this event. */
  matched_scoring_points?: string[]
  matched_deduction_rules?: string[]
  scoring_point_scores?: ScoringPointScore[]
  /** Structured point/rule IDs identified by the consistency audit. */
  audited_rule_ids?: string[]
  model_scoring_point_scores?: ScoringPointScore[]
  model_allocation_status?: string | null
  model_matched_scoring_points?: string[]
  model_matched_deduction_rules?: string[]
  audit_fit?: string | null
  audit_issue_type?: string | null
  audit_reason?: string | null
  expected_score_range?: [number, number] | number[] | null
  recommended_score?: number | null
  recommended_scoring_point_scores?: ScoringPointScore[]
  recommended_matched_scoring_points?: string[]
  recommended_matched_deduction_rules?: string[]
  created_at?: string | null
}

export type AutoGradeJobStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'abstained'
  | 'failed'
  | 'stale'
  | string

export interface AutoGradeJobStatusItem {
  job_id?: string | null
  case_id?: string | null
  report_name?: string | null
  criteria_id: string
  criterion_id?: string | null
  criteria_file_name?: string | null
  rubric_version?: string | null
  status: AutoGradeJobStatus
  diagnosis?: string | null
  reason?: string | null
  decision?: string | null
  error?: string | null
  queued_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  updated_at?: string | null
}

export type ConsistencyLevel = 'calibrating' | 'reliable' | 'unreliable'

export interface ConsistencyTransition {
  transition_id?: string | null
  from?: ConsistencyLevel | string | null
  to?: ConsistencyLevel | string | null
  from_source?: string | null
  to_source?: string | null
  agreement_count?: number
  correction_count?: number
  smoothed_agreement?: number
  trigger?: string | null
  trigger_event_id?: string | null
  created_at?: string | null
}

export interface CriterionConsistencyState {
  record_key: string
  criteria_file_name: string
  criterion_id: string
  criteria_id?: string
  criterion_hash?: string | null
  scorer_revision?: string | null
  state: ConsistencyLevel
  state_source: 'evidence' | 'teacher_override' | string
  evidence_state?: ConsistencyLevel | string | null
  agreement_count: number
  correction_count: number
  smoothed_agreement: number
  threshold: number
  review_rate: number
  effective_review_rate?: number
  base_review_rate?: number
  reviewed_count?: number
  review_required_count?: number
  has_open_conflict?: boolean
  conflict_override?: boolean
  full_batch_recommended?: boolean
  automation_mode?: 'legacy' | 'shadow' | 'adaptive' | string
  override?: Record<string, unknown> | null
  last_transition?: ConsistencyTransition | null
  updated_at?: string | null
}

export interface ReviewAssignment {
  model_event_id: string
  report_name: string
  criterion_id: string
  criteria_id?: string
  criteria_file_name?: string | null
  state_at_assignment?: ConsistencyLevel | string | null
  review_rate: number
  review_required: boolean
  conflict_override?: boolean
  policy_version?: string | null
  reviewed_at?: string | null
  verdict?: 'accepted' | 'corrected' | string | null
}

export interface AlignmentState {
  readiness: AlignmentReadiness[]
  open_conflicts: AlignmentConflict[]
  criterion_authorizations: CriterionAuthorization[]
  criterion_global_summaries: CriterionGlobalSummary[]
  global_report_summaries?: GlobalReportSummary[]
  global_readiness?: GlobalReadiness
  global_projection_hash?: string | null
  auto_grade_jobs: AutoGradeJobStatusItem[]
  auto_grade_batches: AutoGradeBatch[]
  criterion_revisions?: CriterionRevision[]
  audit_jobs?: AutoGradeJobStatusItem[]
  consistency_states?: CriterionConsistencyState[]
  review_assignments?: ReviewAssignment[]
  consistency_automation_mode?: 'legacy' | 'shadow' | 'adaptive' | string
}

export type AutoGradeBatchStatus =
  | 'awaiting_calibration'
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_issues'
  | 'failed'
  | 'superseded'
  | 'cancelling'
  | 'cancelled'

export interface CriterionRevision {
  revision_id: string
  case_id: string
  criteria_file_name: string
  criterion_id: string
  criterion_name?: string | null
  target_rubric_version: string
  target_criterion_hash: string
  status:
    | 'awaiting_audit'
    | 'blocked_by_rubric_update'
    | 'blocked_by_conflict'
    | 'scheduling_regrade'
    | 'regrading'
    | 'completed'
    | 'completed_with_issues'
    | 'superseded'
    | string
  report_names: string[]
  batch_id?: string | null
  created_at: string
  updated_at: string
}

export interface AutoGradeBatchTarget {
  report_name: string
  criterion_id: string
  criterion_name?: string | null
  status: AutoGradeJobStatus
  reason?: string | null
  updated_at?: string | null
}

export interface AutoGradeBatchProgress {
  total: number
  completed: number
  percent: number
  pending: number
  running: number
  done: number
  abstained: number
  failed: number
  stale: number
  superseded?: number
  cancelled?: number
}

export interface AutoGradeBatch {
  batch_id: string
  case_id: string
  criteria_file_name: string
  rubric_version: string
  model_id: string
  status: AutoGradeBatchStatus
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
  cancel_requested_at?: string | null
  cancelled_at?: string | null
  cancelled_by?: string | null
  report_names: string[]
  start_report_name?: string | null
  criteria: Array<{
    criterion_id: string
    criterion_name?: string | null
    max_score?: number | null
  }>
  targets: AutoGradeBatchTarget[]
  progress: AutoGradeBatchProgress
  calibration: {
    required: boolean
    status: 'pending' | 'confirmed'
    rubric_version: string
    confirmed_at?: string | null
    confirmed_by?: string | null
  }
}

export interface AutoGradeAbstention {
  criteria_id: string
  report_name?: string | null
  reason?: string | null
  error?: string | null
  decision?: 'outside' | 'uncertain' | 'inside' | string | null
  lifecycle_state?: AlignmentLifecycle | null
  admission?: 'outside' | 'uncertain' | string | null
}

export interface AutoGradeResponse {
  result?: GradingResult | null
  grades?: GradingItem[]
  abstentions?: AutoGradeAbstention[]
  failures?: AutoGradeAbstention[]
}

export interface EvidenceAnchor {
  anchor_type?: 'text' | 'image' | 'absence'
  text?: string | null
  occurrence?: number | null
  start_offset?: number | null
  end_offset?: number | null
  image_src?: string | null
  image_alt?: string | null
  description?: string | null
  region?: Record<string, number> | null
  expected_location?: string | null
  missing_item?: string | null
}

export type ImplicitGradeStatus =
  | 'uninduced'
  | 'queued'
  | 'inducing'
  | 'proposal_ready'
  | 'confirmed'
  | 'archived'
  | 'failed'

export interface ImplicitGradeItem {
  implicit_grade_id: string
  case_id: string
  report_name: string
  source: string
  teacher_id?: string | null
  criteria_file_name?: string | null
  anchor: EvidenceAnchor
  score: number
  max_score?: number | null
  notes?: string | null
  temporary_label?: string | null
  status: ImplicitGradeStatus
  proposal_id?: string | null
  promoted_criterion_id?: string | null
  confirmed_event_id?: string | null
  source_digest: string
  failure_reason?: string | null
  created_at: string
  updated_at: string
}

export type CriterionProposalStatus = 'pending' | 'confirmed' | 'rejected' | 'superseded' | 'failed'

export interface SourceGradeAssignment {
  implicit_grade_id: string
  generated_score_reason?: string | null
  scoring_point_scores: ScoringPointScore[]
  matched_scoring_points: string[]
  matched_deduction_rules: string[]
  allocation_source: 'model' | 'teacher' | 'derived'
}

export interface CriterionProposalItem {
  proposal_id: string
  proposal_version: number
  case_id: string
  proposal_type: 'create_criterion' | 'update_criterion'
  target_criterion_id?: string | null
  target_criterion_version_at_proposal?: string | null
  source_implicit_grade_ids: string[]
  source_grade_digests: string[]
  source_event_ids?: string[]
  source_event_digests?: string[]
  source_teacher_event_ids?: string[]
  source_model_event_ids?: string[]
  scoring_diff?: Record<string, unknown> | null
  source_grade_assignments: SourceGradeAssignment[]
  status: CriterionProposalStatus
  criteria_file_name: string
  criteria_file_version_at_proposal?: string | null
  suggested_criterion: CriteriaItem
  rubric_patch?: Record<string, unknown> | null
  rationale?: string | null
  model_id?: string | null
  confidence?: number | null
  confirmed_event_ids: string[]
  failure_reason?: string | null
  created_at: string
  updated_at: string
  confirmed_at?: string | null
  confirmed_by?: string | null
}

export interface ApiResponse<T> {
  status: 'ok' | 'error'
  data?: T
  message?: string
}
