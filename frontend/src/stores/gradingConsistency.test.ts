import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { CriteriaItem, CriterionProposalItem } from '../types/schema'
import { useGradingStore } from './grading'

const globalSummary = {
  criteria_file_name: 'rubric.json',
  criterion_id: '1',
  rubric_version: 'rubric-v1',
  max_score: 10,
  total_reports: 3,
  scored_count: 0,
  ungraded_count: 3,
  teacher_count: 0,
  model_count: 0,
  conflict_count: 0,
  migration_conflict_count: 0,
  eligible_count: 3,
  eligible_report_names: ['report-1', 'report-2', 'report-3'] as string[],
  retryable_count: 0,
  retryable_report_names: [] as string[],
  job_counts: {} as Record<string, number>,
  points: [] as unknown[],
}

const alignmentPayload = (state: 'calibrating' | 'reliable' | 'unreliable') => ({
  readiness: {
    criteria: {
      '1': { criterion_id: '1', lifecycle: 'active_selective' },
    } as Record<string, { criterion_id: string; lifecycle: string }>,
  },
  open_conflicts: [],
  criterion_authorizations: [],
  criterion_global_summaries: [globalSummary],
  auto_grade_jobs: [],
  audit_jobs: [],
  consistency_automation_mode: 'adaptive',
  consistency_states: [
    {
      record_key: `record-${state}`,
      criteria_file_name: 'rubric.json',
      criterion_id: '1',
      state,
      state_source: 'evidence',
      agreement_count: state === 'reliable' ? 3 : 0,
      correction_count: state === 'unreliable' ? 1 : 0,
      smoothed_agreement: state === 'reliable' ? 0.8 : state === 'unreliable' ? 1 / 3 : 0.5,
      threshold: 0.8,
      review_rate: state === 'reliable' ? 0.1 : state === 'unreliable' ? 1 : 0.5,
      full_batch_recommended: state === 'reliable',
    },
  ],
  review_assignments: [],
})

type AlignmentPayloadFixture = ReturnType<typeof alignmentPayload>

const addSecondCriterion = (payload: AlignmentPayloadFixture) => {
  payload.readiness.criteria['2'] = {
    criterion_id: '2',
    lifecycle: 'active_selective',
  }
  payload.criterion_global_summaries.push({ ...globalSummary, criterion_id: '2' })
  payload.consistency_states.push({
    ...payload.consistency_states[0]!,
    record_key: `${payload.consistency_states[0]!.record_key}-2`,
    criterion_id: '2',
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
})

describe('adaptive consistency scheduling', () => {
  it('reports a persisted proposal before follow-up reconciliation finishes', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    const suggested: CriteriaItem = {
      criteria_id: '1',
      criteria_name: 'Evidence',
      description: 'Updated evidence quality',
      original_description: 'Evidence quality',
      max_score: 10,
      section: 'analysis',
      few_shot_examples: [],
      scoring_points: [],
      scoring_aggregation: { mode: 'sum_deductions_capped' },
    }
    const proposal: CriterionProposalItem = {
      proposal_id: 'proposal-1',
      proposal_version: 1,
      case_id: 'case-a',
      proposal_type: 'update_criterion',
      target_criterion_id: '1',
      source_implicit_grade_ids: [],
      source_grade_digests: [],
      source_event_ids: ['event-1'],
      source_grade_assignments: [],
      status: 'pending',
      criteria_file_name: 'rubric.json',
      suggested_criterion: suggested,
      confirmed_event_ids: [],
      created_at: '2026-08-12T00:00:00Z',
      updated_at: '2026-08-12T00:00:00Z',
    }
    const confirmed: CriterionProposalItem = {
      ...proposal,
      proposal_version: 2,
      status: 'confirmed',
      confirmed_at: '2026-08-15T00:00:00Z',
    }
    vi.spyOn(api, 'confirmCriterionProposal').mockResolvedValue({
      status: 'ok',
      data: { item: confirmed },
    })

    let releaseCriteriaList!: (value: { status: 'ok'; data: { items: string[] } }) => void
    vi.spyOn(api, 'listCriteria').mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseCriteriaList = resolve
        }),
    )
    vi.spyOn(api, 'getCriteria').mockResolvedValue({
      status: 'ok',
      data: {
        item: {
          schema_version: 'structured-rubric-v1',
          title: 'Rubric',
          background: '',
          language: 'zh',
          base_score: 100,
          criteria: [suggested],
        },
      },
    })
    vi.spyOn(api, 'listImplicitGrades').mockResolvedValue({ status: 'ok', data: { items: [] } })
    vi.spyOn(api, 'listCriterionProposals').mockResolvedValue({
      status: 'ok',
      data: { items: [confirmed] },
    })
    vi.spyOn(api, 'auditCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: { alignment: alignmentPayload('calibrating') },
    })
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('calibrating'),
    })
    const onPersisted = vi.fn()
    let confirmationFinished = false

    const confirmation = store
      .confirmCriterionProposal(proposal, suggested, undefined, { onPersisted })
      .finally(() => {
        confirmationFinished = true
      })

    await vi.waitFor(() => expect(onPersisted).toHaveBeenCalledWith(confirmed))
    expect(confirmationFinished).toBe(false)

    releaseCriteriaList({ status: 'ok', data: { items: ['rubric.json'] } })
    await expect(confirmation).resolves.toEqual(confirmed)
    await flushPromises()
    expect(api.auditCaseAlignment).not.toHaveBeenCalled()
  })

  it('refreshes criterion consistency immediately after saving a rubric change', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [
        {
          criteria_id: '1',
          criteria_name: 'Evidence',
          description: 'Evidence quality',
          original_description: 'Evidence quality',
          max_score: 10,
          section: 'analysis',
          few_shot_examples: [],
          scoring_points: [
            {
              point_id: '1.p1',
              summary: 'Evidence',
              full_credit_condition: 'Evidence is present',
              applies_when: 'Always',
              max_deduction: 10,
              aggregation: 'sum_deductions_capped',
              deduction_rules: [],
            },
          ],
          scoring_aggregation: { mode: 'sum_deductions_capped' },
        },
      ],
    }
    vi.spyOn(api, 'saveCriteria').mockResolvedValue({ status: 'ok', data: { path: 'rubric.json' } })
    vi.spyOn(api, 'listCriteria').mockResolvedValue({ status: 'ok', data: { items: ['rubric.json'] } })
    vi.spyOn(api, 'getCriteria').mockResolvedValue({
      status: 'ok',
      data: { item: store.criteriaSchema },
    })
    const getAlignment = vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('calibrating'),
    })

    await store.saveCriteriaFile()

    expect(getAlignment).toHaveBeenCalledWith('case-a', 'rubric.json')
    expect(store.getCriterionConsistency('1')?.state).toBe('calibrating')
  })

  it('keeps reliable automatic grading idle until the batch start action is used', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.reports = ['report-1', 'report-2', 'report-3'].map((id) => ({
      id,
      name: id,
      markdown: 'report',
      conversion_status: 'finished' as const,
    }))
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('reliable'),
    })
    const autoGrade = vi.spyOn(api, 'autoGradeCaseReport')

    await store.loadAlignment('case-a')
    await flushPromises()

    expect(store.autoGradeBatchStarted).toBe(false)
    expect(autoGrade).not.toHaveBeenCalled()
  })

  it('creates one persisted server batch instead of grading reports from the browser queue', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-1'
    store.reports = ['report-1', 'report-2', 'report-3'].map((id) => ({
      id,
      name: id,
      markdown: 'report',
      conversion_status: 'finished' as const,
    }))
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [
        {
          criteria_id: '1',
          criteria_name: 'Evidence',
          description: 'Evidence quality',
          original_description: 'Evidence quality',
          max_score: 10,
          section: 'analysis',
          few_shot_examples: [],
          scoring_points: [],
          scoring_aggregation: { mode: 'sum_deductions_capped' },
        },
      ],
    }
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('reliable'),
    })
    const autoGrade = vi.spyOn(api, 'autoGradeCaseReport').mockResolvedValue({
      status: 'ok',
      data: { grades: [], abstentions: [], failures: [], result: null },
    })
    const createBatch = vi.spyOn(api, 'createAutoGradeBatch').mockResolvedValue({
      status: 'ok',
      data: {
        batch: {
          batch_id: 'batch-1',
          case_id: 'case-a',
          criteria_file_name: 'rubric.json',
          rubric_version: 'rubric-v1',
          model_id: 'qwen3.7-max',
          status: 'awaiting_calibration',
          created_at: '2026-08-10T00:00:00Z',
          updated_at: '2026-08-10T00:00:00Z',
          report_names: ['report-1', 'report-2', 'report-3'],
          criteria: [{ criterion_id: '1', criterion_name: 'Evidence', max_score: 10 }],
          targets: ['report-1', 'report-2', 'report-3'].map((report_name) => ({
            report_name,
            criterion_id: '1',
            criterion_name: 'Evidence',
            status: 'pending',
          })),
          progress: {
            total: 3,
            completed: 0,
            percent: 0,
            pending: 3,
            running: 0,
            done: 0,
            abstained: 0,
            failed: 0,
            stale: 0,
          },
          calibration: {
            required: true,
            status: 'pending',
            rubric_version: 'rubric-v1',
          },
        },
      },
    })
    await store.loadAlignment('case-a')

    const batch = await store.startBatchAutoGrading()

    expect(createBatch).toHaveBeenCalledWith('case-a', {
      criteria_file_name: 'rubric.json',
      report_names: ['report-1', 'report-2', 'report-3'],
      start_report_name: 'report-1',
    })
    expect(batch && batch.progress.total).toBe(3)
    expect(store.autoGradeBatchStarted).toBe(true)
    expect(store.mode).toBe('single')
    expect(autoGrade).not.toHaveBeenCalled()
  })

  it('loads each completed report result while the persisted batch continues in order', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-1'
    store.reports = ['report-1', 'report-2'].map((id) => ({
      id,
      name: id,
      markdown: 'Report evidence.',
      conversion_status: 'finished' as const,
    }))
    const runningBatch = {
      batch_id: 'batch-1',
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      rubric_version: 'rubric-v1',
      model_id: 'model-a',
      status: 'running' as const,
      created_at: '2026-08-11T00:00:00Z',
      updated_at: '2026-08-11T00:00:01Z',
      report_names: ['report-1', 'report-2'],
      criteria: [{ criterion_id: '1', criterion_name: 'Evidence', max_score: 10 }],
      targets: [
        { report_name: 'report-1', criterion_id: '1', status: 'done' },
        { report_name: 'report-2', criterion_id: '1', status: 'running' },
      ],
      progress: {
        total: 2,
        completed: 1,
        percent: 50,
        pending: 0,
        running: 1,
        done: 1,
        abstained: 0,
        failed: 0,
        stale: 0,
      },
      calibration: {
        required: false,
        status: 'confirmed' as const,
        rubric_version: 'rubric-v1',
      },
    }
    vi.spyOn(api, 'getAutoGradeBatch').mockResolvedValue({
      status: 'ok',
      data: { batch: runningBatch },
    })
    const getAlignment = vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('reliable'),
    })
    const getReport = vi.spyOn(api, 'getCaseReport').mockResolvedValue({
      status: 'ok',
      data: {
        name: 'report-1',
        markdown: 'Report evidence.',
        result: {
          file_name: 'report-1.md',
          language: 'en',
          base_score: 100,
          file_score: 98,
          criteria: [
            {
              criteria_id: '1',
              criteria_name: 'Evidence',
              description: 'Evidence quality',
              original_description: 'Evidence quality',
              max_score: 10,
              criteria_score: 8,
              file_original_content: 'Report evidence.',
              section: 'analysis',
              target_section: 'analysis',
              score_reason: 'Model reason',
              anchor_text: 'Report evidence.',
              evidence_anchors: [{ anchor_type: 'text' as const, text: 'Report evidence.' }],
              graded_by: 'model' as const,
              rubric_file_name: 'rubric.json',
              scoring_points: [],
              scoring_aggregation: { mode: 'sum_deductions_capped' },
            },
          ],
        },
      },
    })

    await store.refreshAutoGradeBatch('batch-1')

    expect(getReport).toHaveBeenCalledTimes(1)
    expect(getReport).toHaveBeenCalledWith('case-a', 'report-1', 'rubric.json')
    expect(store.highlightsByReport['report-1']).toHaveLength(1)
    expect(store.highlightsByReport['report-2']).toBeUndefined()
    expect(store.autoGradeStatusByReport['report-1']).toBe('done')
    expect(store.autoGradeStatusByReport['report-2']).toBe('running')
    expect(getAlignment).toHaveBeenCalledTimes(1)

    await store.refreshAutoGradeBatch('batch-1')
    expect(getReport).toHaveBeenCalledTimes(1)
    expect(getAlignment).toHaveBeenCalledTimes(1)
  })

  it('reloads a partial report when each criterion target completes', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-1'
    store.reports = [
      {
        id: 'report-1',
        name: 'report-1',
        markdown: 'Report evidence.',
        conversion_status: 'finished' as const,
      },
    ]
    const gradingItem = (criteriaId: string) => ({
      criteria_id: criteriaId,
      criteria_name: criteriaId === '1' ? 'Evidence' : 'Analysis',
      description: 'Criterion description',
      original_description: 'Criterion description',
      max_score: 10,
      criteria_score: 8,
      file_original_content: 'Report evidence.',
      section: 'analysis',
      target_section: 'analysis',
      score_reason: 'Model reason',
      anchor_text: 'Report evidence.',
      evidence_anchors: [{ anchor_type: 'text' as const, text: 'Report evidence.' }],
      graded_by: 'model' as const,
      rubric_file_name: 'rubric.json',
      scoring_points: [],
      scoring_aggregation: { mode: 'sum_deductions_capped' },
    })
    const batch = (secondStatus: 'running' | 'done') => ({
      batch_id: 'batch-progressive',
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      rubric_version: 'rubric-v1',
      model_id: 'model-a',
      status: 'running' as const,
      created_at: '2026-08-11T00:00:00Z',
      updated_at: '2026-08-11T00:00:01Z',
      report_names: ['report-1'],
      criteria: [
        { criterion_id: '1', criterion_name: 'Evidence', max_score: 10 },
        { criterion_id: '2', criterion_name: 'Analysis', max_score: 10 },
      ],
      targets: [
        { report_name: 'report-1', criterion_id: '1', status: 'done' as const },
        { report_name: 'report-1', criterion_id: '2', status: secondStatus },
      ],
      progress: {
        total: 2,
        completed: secondStatus === 'done' ? 2 : 1,
        percent: secondStatus === 'done' ? 100 : 50,
        pending: 0,
        running: secondStatus === 'running' ? 1 : 0,
        done: secondStatus === 'done' ? 2 : 1,
        abstained: 0,
        failed: 0,
        stale: 0,
      },
      calibration: {
        required: false,
        status: 'confirmed' as const,
        rubric_version: 'rubric-v1',
      },
    })
    vi.spyOn(api, 'getAutoGradeBatch')
      .mockResolvedValueOnce({ status: 'ok', data: { batch: batch('running') } })
      .mockResolvedValueOnce({ status: 'ok', data: { batch: batch('done') } })
    const getAlignment = vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('reliable'),
    })
    const getReport = vi.spyOn(api, 'getCaseReport')
    getReport
      .mockResolvedValueOnce({
        status: 'ok',
        data: {
          name: 'report-1',
          markdown: 'Report evidence.',
          result: {
            file_name: 'report-1.md',
            language: 'en',
            base_score: 100,
            file_score: 98,
            criteria: [gradingItem('1')],
          },
        },
      })
      .mockResolvedValueOnce({
        status: 'ok',
        data: {
          name: 'report-1',
          markdown: 'Report evidence.',
          result: {
            file_name: 'report-1.md',
            language: 'en',
            base_score: 100,
            file_score: 96,
            criteria: [gradingItem('1'), gradingItem('2')],
          },
        },
      })

    await store.refreshAutoGradeBatch('batch-progressive')
    expect(store.gradingResults['report-1']?.criteria).toHaveLength(1)
    expect(store.autoGradeStatusByReport['report-1']).toBe('running')

    await store.refreshAutoGradeBatch('batch-progressive')
    expect(store.gradingResults['report-1']?.criteria).toHaveLength(2)
    expect(store.autoGradeStatusByReport['report-1']).toBe('done')
    expect(getReport).toHaveBeenCalledTimes(2)
    expect(getAlignment).toHaveBeenCalledTimes(2)
  })

  it('cancels a persisted batch and exposes stopped reports without reloading them', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    const cancelledBatch = {
      batch_id: 'batch-1',
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      rubric_version: 'rubric-v1',
      model_id: 'model-a',
      status: 'cancelled' as const,
      created_at: '2026-08-11T00:00:00Z',
      updated_at: '2026-08-11T00:00:02Z',
      report_names: ['report-1'],
      criteria: [{ criterion_id: '1', criterion_name: 'Evidence', max_score: 10 }],
      targets: [{ report_name: 'report-1', criterion_id: '1', status: 'cancelled' }],
      progress: {
        total: 1,
        completed: 1,
        percent: 100,
        pending: 0,
        running: 0,
        done: 0,
        abstained: 0,
        failed: 0,
        stale: 0,
        cancelled: 1,
      },
      calibration: {
        required: false,
        status: 'confirmed' as const,
        rubric_version: 'rubric-v1',
      },
    }
    const cancelBatch = vi.spyOn(api, 'cancelAutoGradeBatch').mockResolvedValue({
      status: 'ok',
      data: { batch: cancelledBatch },
    })

    await store.cancelAutoGradeBatch('batch-1')

    expect(cancelBatch).toHaveBeenCalledWith('case-a', 'batch-1')
    expect(store.activeAutoGradeBatch?.status).toBe('cancelled')
    expect(store.autoGradeBatchStarted).toBe(false)
  })

  it('does not revive archived adaptive backfill when a criterion becomes reliable', async () => {
    const store = useGradingStore()
    store.autoGradeBatchStarted = true
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.reports = ['report-1', 'report-2', 'report-3'].map((id) => ({
      id,
      name: id,
      markdown: 'report',
      conversion_status: 'finished' as const,
    }))
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: alignmentPayload('reliable'),
    })
    const autoGrade = vi.spyOn(api, 'autoGradeCaseReport').mockResolvedValue({
      status: 'ok',
      data: { grades: [], abstentions: [], failures: [], result: null },
    })

    await store.loadAlignment('case-a')
    await flushPromises()
    expect(autoGrade).not.toHaveBeenCalled()
  })

  it('does not run the archived multi-criterion backfill queue', async () => {
    const store = useGradingStore()
    store.autoGradeBatchStarted = true
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.reports = ['report-1', 'report-2', 'report-3'].map((id) => ({
      id,
      name: id,
      markdown: 'report',
      conversion_status: 'finished' as const,
    }))
    const payload = alignmentPayload('reliable')
    addSecondCriterion(payload)
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: payload,
    })
    const autoGrade = vi.spyOn(api, 'autoGradeCaseReport').mockResolvedValue({
      status: 'ok',
      data: { grades: [], abstentions: [], failures: [], result: null },
    })

    await store.loadAlignment('case-a')
    await flushPromises()
    expect(autoGrade).not.toHaveBeenCalled()
  })

  it.each(['calibrating', 'unreliable'] as const)(
    'does not start full backfill while %s',
    async (state) => {
      const store = useGradingStore()
      store.autoGradeBatchStarted = true
      store.activeCaseId = 'case-a'
      store.activeCriteriaFile = 'rubric.json'
      store.reports = ['report-1', 'report-2', 'report-3'].map((id) => ({
        id,
        name: id,
        markdown: 'report',
        conversion_status: 'finished' as const,
      }))
      vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
        status: 'ok',
        data: alignmentPayload(state),
      })
      const autoGrade = vi.spyOn(api, 'autoGradeCaseReport')

      await store.loadAlignment('case-a')
      await flushPromises()
      expect(autoGrade).not.toHaveBeenCalled()
    },
  )

  it('keeps the archived recovery entry point inert', async () => {
    const store = useGradingStore()
    store.autoGradeBatchStarted = true
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-1'
    store.reports = ['report-1', 'report-2', 'report-3'].map((id) => ({
      id,
      name: id,
      markdown: 'report',
      conversion_status: 'finished' as const,
    }))
    const payload = alignmentPayload('calibrating')
    addSecondCriterion(payload)
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
      status: 'ok',
      data: payload,
    })
    const autoGrade = vi.spyOn(api, 'autoGradeCaseReport').mockResolvedValue({
      status: 'ok',
      data: { grades: [], abstentions: [], failures: [], result: null },
    })

    await store.loadAlignment('case-a')
    await store.recoverAutoGradesForCase()
    await flushPromises()
    expect(autoGrade).not.toHaveBeenCalled()
  })

  it('does not treat a teacher override as full-batch permission during an open conflict', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    const base = alignmentPayload('reliable')
    const payload = {
      ...base,
      criterion_authorizations: [
        {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'batch_authorized',
        },
      ],
      consistency_states: base.consistency_states.map((item) => ({
        ...item,
        has_open_conflict: true,
        full_batch_recommended: false,
      })),
    }
    vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({ status: 'ok', data: payload })

    await store.loadAlignment('case-a')

    expect(store.isCriterionFullBatchAllowed('1')).toBe(false)
  })
})
