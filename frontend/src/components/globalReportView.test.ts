import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CriteriaCard from './CriteriaCard.vue'
import DistributionChart from './DistributionChart.vue'
import ReportView from './ReportView.vue'
import { useGradingStore } from '../stores/grading'
import type { AutoGradeBatch, CriteriaItem, GlobalScorePoint, GradingItem } from '../types/schema'

const criterion: CriteriaItem = {
  criteria_id: '1',
  criteria_name: 'Evidence quality',
  description: 'Evidence supports the conclusion.',
  original_description: 'Evidence supports the conclusion.',
  max_score: 10,
  section: 'analysis',
  few_shot_examples: [],
  scoring_points: [],
  scoring_aggregation: { mode: 'sum_deductions_capped' },
}

const point = (
  reportId: string,
  score: number,
  author: GlobalScorePoint['author'] = 'teacher',
): GlobalScorePoint => ({
  report_id: reportId,
  report_name: reportId,
  criterion_id: '1',
  criteria_id: '1',
  event_id: `evt-${reportId}`,
  score,
  max_score: 10,
  author,
  conflict: reportId === 'report-b',
  anchor_text: `${reportId} evidence`,
  target_section: 'analysis',
  anchor_occurrence: 0,
})

beforeEach(() => {
  setActivePinia(createPinia())
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  })
  Element.prototype.scrollTo = vi.fn()
})

describe('Global score selection', () => {
  it('emits the exact point for mouse and keyboard selection', async () => {
    const target = point('report-a', 8)
    const wrapper = mount(DistributionChart, {
      props: { points: [target], maxScore: 10 },
    })
    const circle = wrapper.get('circle')

    await circle.trigger('click')
    await circle.trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('point-select')).toEqual([[target], [target]])
  })

  it('forwards point selection through CriteriaCard', async () => {
    const target = point('report-a', 8)
    const wrapper = mount(CriteriaCard, {
      props: {
        item: criterion,
        mode: 'global',
        active: true,
        globalSummary: {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          max_score: 10,
          total_reports: 1,
          scored_count: 1,
          ungraded_count: 0,
          average_score: 8,
          teacher_count: 1,
          model_count: 0,
          conflict_count: 0,
          migration_conflict_count: 0,
          eligible_count: 0,
          eligible_report_names: [],
          retryable_count: 0,
          retryable_report_names: [],
          job_counts: {},
          points: [target],
        },
      },
    })

    await wrapper.get('circle').trigger('click')
    expect(wrapper.emitted('point-select')).toEqual([[target]])
  })

  it('atomically updates report and criterion for inline details and renews repeated focus tokens', () => {
    const store = useGradingStore()
    store.activeReportId = 'old-report'
    store.activeCriteriaId = 'old-criterion'
    const target = point('report-a', 8, 'model')

    expect(store.focusGlobalPoint(target)).toBe(true)
    const firstToken = store.globalReportFocus?.token
    expect(store.activeReportId).toBe('report-a')
    expect(store.activeCriteriaId).toBe('1')
    expect(store.globalReportFocus?.eventId).toBe('evt-report-a')

    store.focusGlobalPoint(target)
    expect(store.globalReportFocus?.token).toBeGreaterThan(firstToken || 0)
    expect(store.activeCriteriaId).toBe('1')
  })

  it('uses model provenance for an accepted model grade in Single and Global views', () => {
    const store = useGradingStore()
    store.activeCriteriaFile = 'rubric.json'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'en',
      base_score: 100,
      criteria: [criterion],
    }
    const acceptedModelGrade: GradingItem = {
      ...criterion,
      section: 'analysis',
      criteria_score: 8,
      file_original_content: 'report-a evidence',
      target_section: 'analysis',
      score_reason: 'accepted model reason',
      anchor_text: 'report-a evidence',
      graded_by: 'user',
      acceptance_status: 'accepted_model',
      rubric_file_name: 'rubric.json',
      provenance: {
        event_id: 'evt-report-a',
        grader_type: 'model',
        decision_status: 'accepted_model',
      },
    }
    store.hydrateHighlights('report-a', {
      file_name: 'report-a',
      language: 'en',
      base_score: 100,
      file_score: 98,
      criteria: [acceptedModelGrade],
    })

    expect(store.highlightsByReport['report-a']?.[0]?.source).toBe('llm')
    expect(store.highlightsByReport['report-a']?.[0]?.color).toBe('green')
    expect(store.canAcceptModelGrade(acceptedModelGrade)).toBe(false)
  })
})

describe('Global Report View business rules', () => {
  const prepareStore = () => {
    const store = useGradingStore()
    store.mode = 'global'
    store.activeCriteriaFile = 'rubric.json'
    store.criteriaSchema = {
      schema_version: 'structured-rubric-v1',
      title: 'Rubric',
      background: '',
      language: 'en',
      base_score: 100,
      criteria: [criterion],
    }
    store.activeCriteriaId = '1'
    // Points arrive out of order; cards must follow the case report order.
    store.reports = [
      {
        id: 'report-b',
        name: 'report-b',
        markdown: 'Full report-b evidence paragraph.',
        conversion_status: 'finished',
      },
      {
        id: 'report-a',
        name: 'report-a',
        markdown: 'Full report-a evidence paragraph.',
        conversion_status: 'finished',
      },
    ]
    store.alignment = {
      readiness: [],
      open_conflicts: [],
      criterion_authorizations: [],
      auto_grade_jobs: [],
      auto_grade_batches: [],
      audit_jobs: [],
      criterion_global_summaries: [
        {
          criteria_file_name: 'rubric.json',
          criterion_id: '1',
          max_score: 10,
          total_reports: 3,
          scored_count: 2,
          ungraded_count: 1,
          average_score: 7,
          teacher_count: 1,
          model_count: 1,
          conflict_count: 1,
          migration_conflict_count: 0,
          eligible_count: 1,
          eligible_report_names: ['report-c'],
          retryable_count: 0,
          retryable_report_names: [],
          job_counts: {},
          points: [point('report-a', 8, 'model'), point('report-b', 6)],
        },
      ],
    }
    return store
  }

  it('renders exactly one card per score point in case order and no current-report controls', () => {
    prepareStore()
    const wrapper = mount(ReportView)
    const cards = wrapper.findAll('[data-testid="global-report-card"]')

    expect(cards).toHaveLength(2)
    expect(cards.map((card) => card.attributes('data-report-id'))).toEqual(['report-b', 'report-a'])
    expect(wrapper.text()).toContain('2 evidence cards')
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Previous Report')
    expect(wrapper.findAll('[data-testid="global-evidence-found"]')).toHaveLength(2)
    expect(wrapper.get('[data-report-id="report-a"] .source-badge-model').text()).toBe('Model')
    expect(wrapper.get('[data-report-id="report-b"] .source-badge-teacher').text()).toBe('Teacher')
  })

  it('keeps a fallback card when a scored point has no evidence', () => {
    const store = prepareStore()
    store.alignment.criterion_global_summaries[0]!.points[0]!.anchor_text = ''
    const wrapper = mount(ReportView)

    expect(wrapper.findAll('[data-testid="global-report-card"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="global-evidence-missing"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('This valid grade has no saved evidence text')
  })

  it('focuses the selected card and keeps the selected criterion', async () => {
    const store = prepareStore()
    const wrapper = mount(ReportView, { attachTo: document.body })

    expect(wrapper.get('[data-report-id="report-a"] [data-highlight-id]').classes()).not.toContain(
      'evidence-active-teacher',
    )

    store.focusGlobalPoint(point('report-a', 8, 'model'))
    await nextTick()

    expect(store.activeCriteriaId).toBe('1')
    await vi.waitFor(() => {
      expect(wrapper.get('[data-report-id="report-a"]').classes()).toContain(
        'global-report-card--focused',
      )
      expect(wrapper.get('[data-report-id="report-a"] [data-highlight-id]').classes()).toContain(
        'evidence-active-model',
      )
      expect(Element.prototype.scrollTo).toHaveBeenCalled()
    })
    wrapper.unmount()
  })

  it('uses immediate scrolling and a static focus treatment for reduced motion', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn(() => ({
        matches: true,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    })
    const store = prepareStore()
    const wrapper = mount(ReportView, { attachTo: document.body })

    store.focusGlobalPoint(point('report-b', 6))
    await vi.waitFor(() => expect(Element.prototype.scrollTo).toHaveBeenCalled())

    expect(Element.prototype.scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'auto' }),
    )
    expect(wrapper.get('[data-report-id="report-b"]').classes()).toContain(
      'global-report-card--focused',
    )
    wrapper.unmount()
  })
})

describe('Single Report View auto-grading status', () => {
  const prepareSingleReport = () => {
    const store = useGradingStore()
    store.mode = 'single'
    store.activeReportId = 'report-1'
    store.reports = [
      {
        id: 'report-1',
        name: 'report-1',
        markdown: 'Report evidence.',
        conversion_status: 'finished',
      },
    ]
    const batch: AutoGradeBatch = {
      batch_id: 'batch-1',
      case_id: 'case-a',
      criteria_file_name: 'rubric.json',
      rubric_version: 'rubric-v1',
      model_id: 'model-a',
      status: 'running',
      created_at: '2026-08-11T00:00:00Z',
      updated_at: '2026-08-11T00:00:01Z',
      report_names: ['report-1'],
      criteria: [{ criterion_id: '1' }],
      targets: [{ report_name: 'report-1', criterion_id: '1', status: 'running' }],
      progress: {
        total: 1,
        completed: 0,
        percent: 0,
        pending: 0,
        running: 1,
        done: 0,
        abstained: 0,
        failed: 0,
        stale: 0,
      },
      calibration: {
        required: false,
        status: 'confirmed',
        rubric_version: 'rubric-v1',
      },
    }
    store.activeCriteriaFile = 'rubric.json'
    store.alignment.auto_grade_batches = [batch]
    return { store, batch }
  }

  it('shows progress for the active report without exposing whole-batch progress', () => {
    prepareSingleReport()

    const wrapper = mount(ReportView)

    expect(wrapper.get('[data-testid="report-auto-grading-status"]').text()).toBe(
      'Auto-grading · 0/1',
    )
    expect(wrapper.text()).not.toContain('targets')
    expect(wrapper.text()).not.toContain('%')
  })

  it('shows the first report as complete while the batch continues on a later report', async () => {
    const { store, batch } = prepareSingleReport()
    store.reports.push({
      id: 'report-2',
      name: 'report-2',
      markdown: 'Second report evidence.',
      conversion_status: 'finished',
    })
    store.alignment.auto_grade_batches = [
      {
        ...batch,
        report_names: ['report-1', 'report-2'],
        targets: [
          { report_name: 'report-1', criterion_id: '1', status: 'done' },
          { report_name: 'report-2', criterion_id: '1', status: 'running' },
        ],
        progress: {
          ...batch.progress,
          total: 2,
          completed: 1,
          done: 1,
          running: 1,
          percent: 50,
        },
      },
    ]

    const wrapper = mount(ReportView)
    expect(wrapper.get('[data-testid="report-auto-grading-status"]').text()).toBe(
      'Auto-grading complete · 1/1',
    )
    expect(wrapper.get('[data-testid="report-auto-grading-status"]').classes()).toContain(
      'status-badge-verified',
    )
  })

  it('separates successful scores from terminal failures', () => {
    const { store, batch } = prepareSingleReport()
    store.alignment.auto_grade_batches = [
      {
        ...batch,
        status: 'completed_with_issues',
        criteria: [{ criterion_id: '1' }, { criterion_id: '2' }, { criterion_id: '3' }],
        targets: [
          { report_name: 'report-1', criterion_id: '1', status: 'done' },
          { report_name: 'report-1', criterion_id: '2', status: 'done' },
          { report_name: 'report-1', criterion_id: '3', status: 'failed' },
        ],
        progress: {
          ...batch.progress,
          total: 3,
          completed: 3,
          done: 2,
          failed: 1,
          running: 0,
          percent: 100,
        },
      },
    ]

    const wrapper = mount(ReportView)
    expect(wrapper.get('[data-testid="report-auto-grading-status"]').text()).toBe(
      'Auto-grading finished · 2/3 · 1 failed',
    )
  })

  it('uses the report switch action so old criterion and highlight state are cleared', async () => {
    const { store } = prepareSingleReport()
    store.reports.push({
      id: 'report-2',
      name: 'report-2',
      markdown: 'Second report evidence.',
      conversion_status: 'finished',
    })
    store.activeCriteriaId = '1'
    store.activeHighlightId = 'old-highlight'
    const wrapper = mount(ReportView)

    await wrapper.get('[data-testid="single-report-select"]').setValue('report-2')

    expect(store.activeReportId).toBe('report-2')
    expect(store.activeCriteriaId).toBeNull()
    expect(store.activeHighlightId).toBeNull()
  })
})
