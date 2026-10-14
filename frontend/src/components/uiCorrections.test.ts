import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CriteriaView from './CriteriaView.vue'
import CriteriaCard from './CriteriaCard.vue'
import ScoringPointsDiff from './ScoringPointsDiff.vue'
import StructuredRubricEditor from './StructuredRubricEditor.vue'
import { useGradingStore } from '../stores/grading'
import { api } from '../api/client'
import { validateScoringPoints } from '../utils/scoringPoints'
import type {
  AutoGradeBatch,
  CriteriaItem,
  CriterionProposalItem,
  ScoringPoint,
} from '../types/schema'

const scoringPoint = (
  id: string,
  summary: string,
  condition: string,
  deduct: number,
): ScoringPoint => ({
  point_id: id,
  summary,
  full_credit_condition: `满足 ${summary}`,
  applies_when: '所有报告',
  max_deduction: deduct,
  aggregation: 'sum_deductions_capped',
  deduction_rules: [{ rule_id: `${id}.r1`, condition, deduct, note: '' }],
})

const criterion: CriteriaItem = {
  criteria_id: '1',
  criteria_name: 'Evidence quality',
  description: 'Evidence supports the conclusion.',
  original_description: 'Evidence supports the conclusion.',
  max_score: 10,
  section: 'analysis',
  few_shot_examples: [],
  scoring_points: [scoringPoint('1.p1', '原模型生成的标题', '缺少证据', 10)],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
}

const criterionUpdateProposal: CriterionProposalItem = {
  proposal_id: 'proposal-1',
  proposal_version: 1,
  case_id: 'case-a',
  proposal_type: 'update_criterion',
  target_criterion_id: '1',
  source_implicit_grade_ids: [],
  source_grade_digests: [],
  source_event_ids: ['event-1'],
  source_event_digests: ['digest-1'],
  source_grade_assignments: [],
  status: 'pending',
  criteria_file_name: 'rubric.json',
  suggested_criterion: {
    ...criterion,
    description: 'Evidence A or evidence B supports the conclusion.',
  },
  rationale: 'The teacher correction shows that either alternative is sufficient.',
  confirmed_event_ids: [],
  created_at: '2026-08-15T00:00:00Z',
  updated_at: '2026-08-15T00:00:00Z',
}

beforeEach(() => {
  setActivePinia(createPinia())
  HTMLElement.prototype.scrollIntoView = vi.fn()
})

describe('UI language policy', () => {
  it('keeps hard-coded component and validation copy in English', () => {
    const uiSources = import.meta.glob<string>(['./*.vue', '../utils/scoringPoints.ts'], {
      eager: true,
      query: '?raw',
      import: 'default',
    })

    for (const [file, source] of Object.entries(uiSources)) {
      expect(source, file).not.toMatch(/[\u3400-\u9fff]/u)
    }
  })
})

describe('scoring point update presentation', () => {
  it('shows content and score changes without internal identifiers or rules', () => {
    const before = [scoringPoint('1.p1', '原模型生成的标题', '缺少证据', 10)]
    const after = [scoringPoint('1.p1', '更新后的内容', '证据不充分', 8)]
    const wrapper = mount(ScoringPointsDiff, { props: { before, after } })

    expect(wrapper.text()).toContain('Before')
    expect(wrapper.text()).toContain('After')
    expect(wrapper.text()).toContain('原模型生成的标题')
    expect(wrapper.text()).toContain('更新后的内容')
    expect(wrapper.findAll('[data-testid="before-after-pair"]').length).toBeGreaterThan(0)
    expect(wrapper.find('.line-through').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('1.p1')
    expect(wrapper.text()).not.toContain('Deduction rule')
    expect(wrapper.text()).not.toContain('证据不充分')
  })

  it('uses scoring-point content instead of a backend sequence number', () => {
    const first = scoringPoint('1.p1', '第一项', '条件一', 5)
    const second = scoringPoint('1.p2', '第二项', '条件二', 5)
    const wrapper = mount(ScoringPointsDiff, {
      props: {
        before: [first, second],
        after: [first, { ...second, summary: '第二项更新' }],
      },
    })

    expect(wrapper.get('[data-testid="scoring-point-change"]').text()).toContain('第二项更新')
    expect(wrapper.text()).not.toContain('1.p2')
  })

  it('does not expose internal-only rule changes', () => {
    const point = scoringPoint('1.p1', '评分内容', '条件一', 5)
    point.deduction_rules.push({
      rule_id: '1.p1.r2',
      condition: '条件二',
      deduct: 5,
      note: '',
    })
    const updated = { ...point, deduction_rules: [point.deduction_rules[0]!] }
    const wrapper = mount(ScoringPointsDiff, {
      props: { before: [point], after: [updated] },
    })

    expect(wrapper.text()).toContain('No scoring point changes.')
    expect(wrapper.text()).not.toContain('Deduction rule')
    expect(wrapper.text()).not.toContain('条件二')
  })
})

describe('scoring point editor presentation', () => {
  it('edits only content and score while maintaining internal rule data', async () => {
    const wrapper = mount(StructuredRubricEditor, {
      props: { modelValue: [scoringPoint('1.p1', '评分内容', '旧条件', 5)], criterionId: '1' },
    })

    expect(wrapper.text()).not.toContain('1.p1')
    expect(wrapper.text()).not.toContain('1.p1.r1')
    expect(wrapper.text()).not.toContain('Deduction rule')

    await wrapper.get('textarea').setValue('更新后的评分内容')
    await wrapper.get('input[type="number"]').setValue('8')

    const updates = wrapper.emitted('update:modelValue') || []
    expect(updates[updates.length - 1]?.[0]).toEqual([
      expect.objectContaining({
        point_id: '1.p1',
        summary: '更新后的评分内容',
        max_deduction: 8,
        deduction_rules: [
          expect.objectContaining({
            rule_id: '1.p1.r1',
            condition: '更新后的评分内容',
            deduct: 8,
          }),
        ],
      }),
    ])
  })

  it('keeps backend field names out of user-facing validation messages', () => {
    const errors = validateScoringPoints([
      { point_id: '', summary: '评分内容', max_deduction: 2, deduction_rules: [] },
    ])

    expect(errors.join(' ')).not.toMatch(/point_id|rule_id|deduction rule|aggregation/i)
  })
})

describe('rubric update review entry', () => {
  it('asks for confirmation on the criterion card', async () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        pendingProposals: [criterionUpdateProposal],
      },
    })

    await wrapper.get('[data-testid="review-criterion-proposal-1"]').trigger('click')

    expect(wrapper.emitted('review-proposal')).toEqual([[criterionUpdateProposal]])
  })

  it('does not open or duplicate a targeted proposal until the card action is clicked', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    store.criterionProposals = [criterionUpdateProposal]
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="proposal-review-modal"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="review-proposal"]').exists()).toBe(false)

    await wrapper.get('[data-testid="review-criterion-proposal-1"]').trigger('click')

    const modal = wrapper.get('[data-testid="proposal-review-modal"]')
    expect(modal).toBeDefined()
    expect(modal.text()).not.toContain('(Criterion 1)')
    expect(modal.text()).not.toContain('1.p1')
  })
})

describe('criterion consistency presentation', () => {
  it.each([
    ['calibrating', 'Calibrating Insufficient samples', 'status-fill-neutral'],
    ['reliable', 'Reliable consistency 80%', 'status-fill-verified'],
    ['unreliable', 'Review needed 60%', 'status-fill-review'],
  ] as const)(
    'renders %s independently from evidence highlight colors',
    (state, label, cssClass) => {
      const rate = state === 'reliable' ? 0.8 : state === 'unreliable' ? 0.6 : 0.5
      const wrapper = mount(CriteriaCard, {
        props: {
          item: criterion,
          mode: 'single',
          active: false,
          consistency: {
            record_key: `record-${state}`,
            criteria_file_name: 'rubric.json',
            criterion_id: '1',
            state,
            state_source: 'evidence',
            agreement_count: state === 'calibrating' ? 0 : 2,
            correction_count: state === 'unreliable' ? 1 : 0,
            smoothed_agreement: rate,
            threshold: 0.8,
            review_rate: state === 'reliable' ? 0.1 : state === 'unreliable' ? 1 : 0.5,
          },
        },
      })

      const badge = wrapper.get('[data-testid="consistency-badge-1"]')
      expect(badge.attributes('aria-label')).toBe(label)
      expect(badge.get('[role="meter"] > div').classes()).toContain(cssClass)
    },
  )

  it('keeps the description visible and replaces normal-state tags with compact visuals', () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        grading: {
          ...criterion,
          criteria_score: 8,
          file_original_content: 'Evidence',
          section: 'analysis',
          target_section: 'analysis',
          score_reason: 'Grounded',
        },
        authorization: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'batch_authorized',
        },
        readiness: { criteria_id: '1', lifecycle_state: 'active_selective' },
      },
    })

    expect(wrapper.text()).toContain(criterion.description)
    expect(wrapper.get('[data-testid="criterion-score-visual-1"]').text()).toContain('8/10')
    expect(wrapper.text()).not.toContain('Active selective')
    expect(wrapper.text()).not.toContain('Model authorized')
    expect(wrapper.find('[data-testid="inspect-evidence-1"]').exists()).toBe(false)
  })

  it('shows a missing indicator only after auto-grading finished without a grade', () => {
    const missing = mount(CriteriaCard, {
      props: { item: criterion, mode: 'single', active: false },
    })
    expect(missing.find('[data-testid="not-auto-graded-1"]').exists()).toBe(false)
    expect(missing.classes()).not.toContain('border-amber-300')
    expect(missing.text()).toContain('—/10')

    const failed = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        autoGradeJob: {
          criteria_id: '1',
          report_name: 'report-a',
          status: 'failed',
        },
        authorization: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'teacher_validation',
        },
        readiness: { criteria_id: '1', lifecycle_state: 'active_selective' },
        globalSummary: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          max_score: 10,
          total_reports: 3,
          scored_count: 1,
          ungraded_count: 2,
          teacher_count: 1,
          model_count: 0,
          conflict_count: 0,
          migration_conflict_count: 0,
          eligible_count: 2,
          eligible_report_names: ['report-b', 'report-c'],
          retryable_count: 1,
          retryable_report_names: ['report-a'],
          job_counts: { failed: 1 },
          points: [],
        },
      },
    })
    const missedBadge = failed.get('[data-testid="not-auto-graded-1"]')
    const gradeRemaining = failed.get('[data-testid="grade-remaining-1"]')
    expect(missedBadge.text()).toBe('Not auto-graded')
    expect(failed.classes()).toContain('border-amber-300')
    expect(gradeRemaining.text()).toContain('Grade remaining 2 reports')
    expect(gradeRemaining.text()).toContain('Consistency not yet reliable')
    expect(gradeRemaining.classes()).toContain('border-amber-300')
    expect(missedBadge.element.parentElement).toBe(
      failed.get('[data-testid="criteria-card-header-meta-1"]').element,
    )
    expect(missedBadge.element.parentElement).not.toBe(gradeRemaining.element.parentElement)

    const zero = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        grading: {
          ...criterion,
          criteria_score: 0,
          file_original_content: 'No supporting evidence.',
          section: 'analysis',
          target_section: 'analysis',
          score_reason: 'No credit.',
        },
      },
    })
    expect(zero.find('[data-testid="not-auto-graded-1"]').exists()).toBe(false)
    expect(zero.text()).toContain('0/10')
  })

  it('uses green for grading remaining reports only after consistency is reliable', () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        authorization: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'teacher_validation',
        },
        readiness: { criteria_id: '1', lifecycle_state: 'active_selective' },
        consistency: {
          record_key: 'record-reliable',
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'reliable',
          state_source: 'evidence',
          agreement_count: 3,
          correction_count: 0,
          smoothed_agreement: 0.8,
          threshold: 0.8,
          review_rate: 0.1,
        },
      },
    })

    const gradeRemaining = wrapper.get('[data-testid="grade-remaining-1"]')
    expect(gradeRemaining.classes()).toContain('border-emerald-200')
    expect(gradeRemaining.text()).not.toContain('Consistency not yet reliable')
    expect(gradeRemaining.attributes('title')).toBe(
      'Consistency has reached the reliability threshold',
    )
  })

  it('offers model pre-grade acceptance as a direct card action', async () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        canAcceptModelGrade: true,
      },
    })

    await wrapper.get('[data-testid="accept-model-pregrade-1"]').trigger('click')
    expect(wrapper.emitted('accept-model')).toEqual([['1']])
  })

  it('places a sampled review requirement before edit and hides feedback counts', () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        consistency: {
          record_key: 'record-override',
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'reliable',
          state_source: 'teacher_override',
          agreement_count: 1,
          correction_count: 0,
          smoothed_agreement: 2 / 3,
          threshold: 0.8,
          review_rate: 0.1,
        },
        reviewAssignment: {
          model_event_id: 'model-1',
          report_name: 'report-1',
          criterion_id: '1',
          review_rate: 0.5,
          review_required: true,
        },
      },
    })

    const reviewNotice = wrapper.get('[data-testid="review-required-1"]')
    const editButton = wrapper.get('[data-testid="edit-criterion-1"]')
    const scoreVisual = wrapper.get('[data-testid="criterion-score-visual-1"]')
    expect(reviewNotice.text()).toBe('Need review')
    expect(reviewNotice.attributes('title')).toContain('50% consistency review sample')
    expect(reviewNotice.element.parentElement).toBe(editButton.element.parentElement)
    expect(
      Array.from(reviewNotice.element.parentElement?.children || []).indexOf(reviewNotice.element),
    ).toBeLessThan(
      Array.from(editButton.element.parentElement?.children || []).indexOf(editButton.element),
    )
    expect(wrapper.text()).not.toContain('Accepted')
    expect(wrapper.text()).not.toContain('Corrected')
    expect(wrapper.text()).toContain('Teacher override')
    expect(scoreVisual.find('[data-testid="criterion-provenance-1"]').exists()).toBe(true)
  })

  it('keeps every global status tag in the title-side metadata area', () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'global',
        active: false,
        readiness: { criteria_id: '1', lifecycle_state: 'active_selective' },
        conflicts: [{ criteria_id: '1', status: 'open', diagnosis: 'score_rubric' }],
        authorization: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'batch_authorized',
        },
      },
    })

    const header = wrapper.get('[data-testid="criteria-card-header-meta-1"]').element
    expect(wrapper.get('[data-testid="criteria-status-1-lifecycle"]').element.parentElement).toBe(
      header,
    )
    expect(wrapper.get('[data-testid="criteria-status-1-conflict"]').element.parentElement).toBe(
      header,
    )
    expect(
      wrapper.get('[data-testid="criteria-status-1-authorization"]').element.parentElement,
    ).toBe(header)
  })

  it('shows that an open conflict pauses an existing batch authorization', () => {
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'single',
        active: false,
        authorization: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          state: 'batch_authorized',
        },
        conflicts: [{ criteria_id: '1', status: 'open', diagnosis: 'score_rubric' }],
      },
    })

    expect(wrapper.text()).toContain('Automation paused by conflict')
    expect(wrapper.text()).not.toContain('Model authorized')
  })
})

describe('implicit rubric induction', () => {
  it('explains an incomplete model response and retries the saved teacher grade', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case 1'
    store.activeCriteriaFile = 'New Criteria.json'
    store.activeReportId = '穆高山'
    store.reports = [
      {
        id: '穆高山',
        name: '穆高山',
        markdown: 'Selected report evidence.',
        conversion_status: 'finished',
      },
    ]
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'New Criteria',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [],
    }
    const failedItem = {
      implicit_grade_id: 'ig-failed',
      case_id: 'case 1',
      report_name: '穆高山',
      source: 'teacher',
      criteria_file_name: 'New Criteria.json',
      anchor: {
        anchor_type: 'text' as const,
        text: 'Selected report evidence.',
      },
      score: 4,
      status: 'failed' as const,
      source_digest: 'digest',
      failure_reason: 'ValidationError: scoring_points and scoring_aggregation are invalid',
      created_at: '2026-08-15T00:00:00Z',
      updated_at: '2026-08-15T00:00:00Z',
    }
    store.implicitGrades = [failedItem]
    const retry = vi.spyOn(api, 'retryImplicitGrade').mockResolvedValue({
      status: 'ok',
      data: {
        item: {
          ...failedItem,
          status: 'queued',
          failure_reason: null,
          updated_at: '2026-08-15T00:01:00Z',
        },
      },
    })

    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    expect(wrapper.text()).toContain(
      'The model returned incomplete scoring details. Your evidence and score were kept.',
    )
    await wrapper.get('[data-testid="retry-uninduced-ig-failed"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(retry).toHaveBeenCalledWith('case 1', 'ig-failed')
    expect(store.implicitGrades[0]?.status).toBe('queued')
    expect(store.implicitGrades[0]?.score).toBe(4)
    expect(store.implicitGrades[0]?.anchor.text).toBe('Selected report evidence.')
  })
})

describe('conflict model suggestion action placement', () => {
  it('does not expose the legacy model-recommendation conflict action', () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    store.alignment = {
      readiness: [],
      criterion_authorizations: [],
      criterion_global_summaries: [],
      auto_grade_jobs: [],
      auto_grade_batches: [],
      audit_jobs: [],
      open_conflicts: [
        {
          conflict_id: 'conflict-1',
          conflict_type: 'score_rubric',
          criteria_id: '1',
          reason: '模型发现当前评分与标准不一致',
          open: true,
          suspect_events: [
            {
              event_id: 'event-1',
              report_name: 'report-a.md',
              score: 4,
              recommended_score: 8,
              audit_reason: '证据满足评分点要求',
              anchor_text: '对应证据段落',
              audited_rule_ids: ['1.p1', '1.p1.r1'],
            },
          ],
        },
      ],
    }
    const save = vi.spyOn(store, 'saveGradeDirect').mockResolvedValue(null)
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    expect(wrapper.text()).not.toContain('Accept model recommendation')
    expect(save).not.toHaveBeenCalled()
  })
})

describe('manual auto-grading entry point', () => {
  it('enters Global View immediately and keeps calibration available there', async () => {
    const store = useGradingStore()
    store.activeCaseId = 'case-a'
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.reports = [
      {
        id: 'report-a',
        name: 'report-a',
        markdown: 'Report body',
        conversion_status: 'finished',
      },
    ]
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion, { ...criterion, criteria_id: '2', criteria_name: 'Conclusion' }],
    }
    const awaitingBatch: AutoGradeBatch = {
      batch_id: 'batch-1',
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      rubric_version: 'rubric-v1',
      model_id: 'qwen3.7-max',
      status: 'awaiting_calibration',
      created_at: '2026-08-10T00:00:00Z',
      updated_at: '2026-08-10T00:00:00Z',
      report_names: ['report-a'],
      criteria: [
        {
          criterion_id: '1',
          criterion_name: 'Evidence quality',
          max_score: 10,
        },
        { criterion_id: '2', criterion_name: 'Conclusion', max_score: 10 },
      ],
      targets: [
        {
          report_name: 'report-a',
          criterion_id: '1',
          criterion_name: 'Evidence quality',
          status: 'pending',
        },
        {
          report_name: 'report-a',
          criterion_id: '2',
          criterion_name: 'Conclusion',
          status: 'pending',
        },
      ],
      progress: {
        total: 2,
        completed: 0,
        percent: 0,
        pending: 2,
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
    }
    const startBatch = vi.spyOn(store, 'startBatchAutoGrading').mockImplementation(async () => {
      store.alignment.auto_grade_batches = [awaitingBatch]
      store.autoGradeBatchStarted = true
      return awaitingBatch
    })
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    await wrapper.get('[data-testid="start-auto-grading"]').trigger('click')

    await vi.waitFor(() => expect(startBatch).toHaveBeenCalledOnce())
    expect(store.mode).toBe('global')
    await vi.waitFor(() =>
      expect(wrapper.get('[data-testid="auto-grade-feedback"]').text()).toContain('Batch prepared'),
    )
    expect(wrapper.find('[data-testid="inline-batch-calibration"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="inline-batch-progress"]').exists()).toBe(false)
  })
})

describe('model evidence navigation', () => {
  it('jumps past a real zero score to the next criterion without a grade record', async () => {
    const store = useGradingStore()
    const missingCriterion = {
      ...criterion,
      criteria_id: '2',
      criteria_name: 'Conclusion',
    }
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.activeCriteriaId = '1'
    store.reports = [
      {
        id: 'report-a',
        name: 'report-a',
        markdown: 'Report body.',
        conversion_status: 'finished',
      },
    ]
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion, missingCriterion],
    }
    store.gradingResults['report-a'] = {
      file_name: 'report-a.md',
      language: 'zh',
      base_score: 100,
      file_score: 90,
      criteria: [
        {
          ...criterion,
          criteria_score: 0,
          file_original_content: 'No supporting evidence.',
          section: 'analysis',
          target_section: 'analysis',
          score_reason: 'No credit.',
        },
      ],
    }

    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="jump-next-ungraded"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="not-auto-graded-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="not-auto-graded-2"]').exists()).toBe(false)

    store.alignment.auto_grade_jobs = [
      {
        criteria_id: '2',
        report_name: 'report-a',
        criteria_file_name: 'rubric.json',
        status: 'failed',
      },
    ]
    await wrapper.vm.$nextTick()

    const jump = wrapper.get('[data-testid="jump-next-ungraded"]')
    expect(jump.text()).toContain('Next ungraded · 1')
    expect(wrapper.get('[data-testid="criteria-status-2-notice"]').text()).toBe('Failed')
    expect(wrapper.find('[data-testid="not-auto-graded-2"]').exists()).toBe(false)

    await jump.trigger('click')
    await wrapper.vm.$nextTick()

    expect(store.activeCriteriaId).toBe('2')
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled()
  })

  it('merges adjacent grounded text anchors and focuses the combined segment', async () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.reports = [
      {
        id: 'report-a',
        name: 'report-a',
        markdown: 'First exact passage.\n\nSecond exact passage.',
        conversion_status: 'finished',
      },
    ]
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    store.hydrateHighlights('report-a', {
      file_name: 'report-a.md',
      language: 'zh',
      base_score: 100,
      file_score: 98,
      criteria: [
        {
          ...criterion,
          section: 'analysis',
          criteria_score: 8,
          file_original_content: 'Legacy multi-section model output that is not a report quote.',
          target_section: 'analysis',
          score_reason: 'Two passages support this score.',
          anchor_text: 'Legacy multi-section model output that is not a report quote.',
          evidence_anchors: [
            {
              anchor_type: 'text',
              text: 'First exact passage.',
              occurrence: 0,
            },
            {
              anchor_type: 'text',
              text: 'Second exact passage.',
              occurrence: 0,
            },
          ],
          graded_by: 'model',
          provenance: { event_id: 'evt-model-1', grader_type: 'model' },
        },
      ],
    })
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    expect(store.reportHighlights.map((item) => item.anchor.text)).toEqual([
      'First exact passage.\n\nSecond exact passage.',
    ])
    expect(store.reportHighlights.map((item) => item.id)).toEqual(['event-evt-model-1'])

    await wrapper.get('[data-criterion-id="1"]').trigger('click')

    expect(store.activeHighlightId).toBe('event-evt-model-1')
  })

  it('offers separate controls for non-contiguous evidence and focuses the chosen segment', async () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.reports = [
      {
        id: 'report-a',
        name: 'report-a',
        markdown: 'First exact passage.\n\nUnrelated paragraph.\n\nSecond exact passage.',
        conversion_status: 'finished',
      },
    ]
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    store.hydrateHighlights('report-a', {
      file_name: 'report-a.md',
      language: 'zh',
      base_score: 100,
      file_score: 98,
      criteria: [
        {
          ...criterion,
          section: 'analysis',
          criteria_score: 8,
          file_original_content: 'Legacy output.',
          target_section: 'analysis',
          score_reason: 'Two separate passages support this score.',
          anchor_text: 'Legacy output.',
          evidence_anchors: [
            {
              anchor_type: 'text',
              text: 'First exact passage.',
              occurrence: 0,
            },
            {
              anchor_type: 'text',
              text: 'Second exact passage.',
              occurrence: 0,
            },
          ],
          graded_by: 'model',
          provenance: { event_id: 'evt-model-segments', grader_type: 'model' },
        },
      ],
    })
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    expect(store.reportHighlights).toHaveLength(2)
    expect(wrapper.find('[data-testid="evidence-segment-selector"]').exists()).toBe(true)
    await wrapper.get('[data-criterion-id="1"]').trigger('click')
    await wrapper.get('[data-testid="evidence-segment-1-2"]').trigger('click')

    expect(store.activeCriteriaId).toBe('1')
    expect(store.activeHighlightId).toBe('event-evt-model-segments-anchor-1')
  })

  it('requests evidence focus again when the selected card is clicked repeatedly', async () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.reports = [
      {
        id: 'report-a',
        name: 'report-a',
        markdown: 'Exact passage.',
        conversion_status: 'finished',
      },
    ]
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    store.hydrateHighlights('report-a', {
      file_name: 'report-a.md',
      language: 'zh',
      base_score: 100,
      file_score: 98,
      criteria: [
        {
          ...criterion,
          section: 'analysis',
          criteria_score: 8,
          file_original_content: 'Exact passage.',
          target_section: 'analysis',
          score_reason: 'Grounded.',
          anchor_text: 'Exact passage.',
          evidence_anchors: [{ anchor_type: 'text', text: 'Exact passage.', occurrence: 0 }],
          graded_by: 'model',
          provenance: { event_id: 'evt-repeat', grader_type: 'model' },
        },
      ],
    })
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })
    const card = wrapper.get('[data-criterion-id="1"]')

    await card.trigger('click')
    const firstToken = store.highlightFocusToken
    await card.trigger('click')

    expect(store.activeCriteriaId).toBe('1')
    expect(store.activeHighlightId).toBe('event-evt-repeat')
    expect(store.highlightFocusToken).toBe(firstToken + 1)
  })

  it('clears the selected card and evidence focus when the criteria list background is clicked', async () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    await wrapper.get('[data-criterion-id="1"]').trigger('click')
    expect(store.activeCriteriaId).toBe('1')

    await wrapper.get('[data-testid="criteria-list"]').trigger('click')

    expect(store.activeCriteriaId).toBeNull()
    expect(store.activeHighlightId).toBeNull()
  })

  it('clears the selected card when another control in the criteria pane is clicked', async () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })

    await wrapper.get('[data-criterion-id="1"]').trigger('click')
    await wrapper.get('[data-testid="criteria-section-toggle"]').trigger('click')

    expect(store.activeCriteriaId).toBeNull()
  })

  it('keeps the card selected when its contents are clicked', async () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.activeReportId = 'report-a'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    const wrapper = mount(CriteriaView, {
      global: {
        stubs: {
          ReportAssessmentRow: true,
          ScoringPointsDiff: true,
          StructuredRubricEditor: true,
        },
      },
    })
    const card = wrapper.get('[data-criterion-id="1"]')

    await card.trigger('click')
    await card.get('report-assessment-row-stub').trigger('click')

    expect(store.activeCriteriaId).toBe('1')
  })

  it('does not turn an absence-only model finding into a whole-document highlight', () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'zh',
      base_score: 100,
      criteria: [criterion],
    }
    store.hydrateHighlights('report-a', {
      file_name: 'report-a.md',
      language: 'zh',
      base_score: 100,
      file_score: 90,
      criteria: [
        {
          ...criterion,
          section: 'analysis',
          criteria_score: 0,
          file_original_content: 'Entire report fallback',
          target_section: 'analysis',
          score_reason: 'Required evidence is absent.',
          anchor_text: 'Entire report fallback',
          evidence_anchors: [
            {
              anchor_type: 'absence',
              text: '',
              missing_item: 'Required conclusion',
              expected_location: 'document:end',
            },
          ],
          graded_by: 'model',
          provenance: { event_id: 'evt-model-absence', grader_type: 'model' },
        },
      ],
    })

    expect(store.reportHighlights).toEqual([])
  })
})
