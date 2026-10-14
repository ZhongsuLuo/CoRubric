import { DOMWrapper, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import App from '../App.vue'
import { api } from '../api/client'
import { useAutoGradeBatchController } from '../composables/useAutoGradeBatchController'
import { criterion, prepareGlobalViewStore, setupGlobalViewTest } from './globalViewTestFixture'

beforeEach(setupGlobalViewTest)

describe('Page-level view guard', () => {
  it('does not reset the experiment when confirmation is cancelled', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.activeCaseId = 'case 1'
    store.activeCriteriaFile = store.getCaseDefaultCriteria('case 1') || ''
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const resetSpy = vi.spyOn(store, 'resetCurrentExperiment')
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = mount(App)

    await wrapper.get('[data-testid="debug-reset-experiment"]').trigger('click')

    expect(resetSpy).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="experiment-reset-message"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('restores the configured experiment only after explicit confirmation', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.activeCaseId = 'case 1'
    store.activeCriteriaFile = store.getCaseDefaultCriteria('case 1') || ''
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const resetSpy = vi.spyOn(store, 'resetCurrentExperiment').mockResolvedValue({
      case_id: 'case 1',
      criteria_file_name: store.activeCriteriaFile,
      rubric_version: 'baseline-hash',
      cleared: { events: 3 },
      restored: { files: 0 },
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(App)

    await wrapper.get('[data-testid="debug-reset-experiment"]').trigger('click')
    await vi.waitFor(() => expect(resetSpy).toHaveBeenCalledOnce())

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('case 1'))
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('configured baseline'))
    expect(wrapper.get('[data-testid="experiment-reset-message"]').text()).toContain(
      'Reset case 1 to its experiment baseline.',
    )
    wrapper.unmount()
  })

  it('does not expose experiment reset for an unconfigured case', () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const wrapper = mount(App)

    expect(wrapper.find('[data-testid="debug-reset-experiment"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('uses a two-column workspace, inline rubric editing, and an advanced modal', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.activeReportId = 'report-a'
    store.activeCriteriaId = '1'
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)

    const wrapper = mount(App)
    const layout = wrapper.get('[data-testid="report-view-layout"]')
    const workspace = layout.element.parentElement
    const relationOverlay = layout.get('[data-testid="evidence-relation-overlay"]')
    const workspaceSections = layout.findAll(':scope > section')

    expect(workspaceSections).toHaveLength(2)
    expect(layout.classes()).toContain('grid-cols-[minmax(0,2fr)_minmax(0,3fr)]')
    expect(layout.classes()).toContain('gap-4')
    expect(workspace?.classList).toContain('pt-2')
    expect(workspace?.classList).toContain('sm:pt-3')
    workspaceSections.forEach((section) => {
      expect(section.classes()).not.toContain('rounded-2xl')
      expect(section.classes()).not.toContain('shadow-sm')
      expect(section.classes()).not.toContain('ring-1')
    })
    expect(relationOverlay.element.parentElement).toBe(layout.element)
    expect(document.body.querySelector('[data-testid="criteria-editor-modal"]')).toBeNull()

    await wrapper.get('[data-testid="edit-criterion-1"]').trigger('click')
    const inlineEditor = wrapper.get('[data-testid="inline-rubric-editor-1"]')
    expect(inlineEditor.isVisible()).toBe(true)
    const advancedButton = inlineEditor
      .findAll('button')
      .find((button) => button.text().includes('Advanced edit'))
    expect(advancedButton).toBeDefined()
    await advancedButton!.trigger('click')
    const modalElement = document.body.querySelector('[data-testid="criteria-editor-modal"]')
    expect(modalElement).not.toBeNull()
    const modal = new DOMWrapper(modalElement as Element)
    expect(modal.get('[data-testid="editor-report-panel"]').isVisible()).toBe(false)
    expect(modal.get('[data-testid="editor-criteria-panel"]').isVisible()).toBe(true)

    await modal.get('[data-testid="close-criteria-editor"]').trigger('click')
    expect(document.body.querySelector('[data-testid="criteria-editor-modal"]')).toBeNull()
    wrapper.unmount()
  })

  it('saves a teacher grade from the selected table row', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.activeReportId = 'report-a'
    store.activeCriteriaId = '1'
    store.criteriaSchema!.criteria[0]!.scoring_points[0]!.deduction_rules = [
      {
        rule_id: '1.p1.missing',
        condition: 'Required evidence is missing.',
        deduct: 10,
      },
    ]
    store.gradingResults['report-a'] = {
      file_name: 'report-a.md',
      language: 'zh',
      base_score: 100,
      file_score: 96,
      criteria: [
        {
          ...store.criteriaSchema!.criteria[0]!,
          criteria_score: 6,
          file_original_content: 'selected evidence',
          section: 'analysis',
          target_section: 'analysis',
          score_reason: 'Grounded reason',
          scoring_point_scores: [{ point_id: '1.p1', score: 6, max_score: 10 }],
        },
      ],
    }
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const saveSpy = vi.spyOn(store, 'saveGradeDirect').mockResolvedValue(null)
    const wrapper = mount(App)

    await wrapper.get('[data-testid="inline-point-score-1.p1"]').setValue('7')
    await wrapper.get('[data-testid="save-inline-grade-1"]').trigger('click')

    await vi.waitFor(() => expect(saveSpy).toHaveBeenCalledOnce())
    expect(saveSpy).toHaveBeenCalledWith(expect.objectContaining({ score: 7, criteriaId: '1' }))
    expect(document.body.querySelector('[data-testid="criteria-editor-modal"]')).toBeNull()
    wrapper.unmount()
  })

  it('updates a scoring standard from the selected table row', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.activeReportId = 'report-a'
    store.activeCriteriaId = '1'
    store.criteriaSchema!.criteria[0]!.scoring_points[0]!.deduction_rules = [
      {
        rule_id: '1.p1.missing',
        condition: 'Required evidence is missing.',
        deduct: 10,
      },
    ]
    store.criteriaSchema!.criteria[1]!.scoring_points[0]!.deduction_rules = [
      {
        rule_id: '2.p1.missing',
        condition: 'Required evidence is missing.',
        deduct: 5,
      },
    ]
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const saveSpy = vi.spyOn(store, 'saveCriteriaFile').mockResolvedValue(undefined)
    const wrapper = mount(App)

    await wrapper.get('[data-testid="edit-criterion-1"]').trigger('click')
    await wrapper.get('[data-testid="save-inline-rubric-1"]').trigger('click')

    await vi.waitFor(() => expect(saveSpy).toHaveBeenCalledOnce())
    expect(wrapper.get('[data-testid="inline-rubric-editor-1"]').isVisible()).toBe(true)
    wrapper.unmount()
  })

  it('refreshes alignment while a rubric revision is waiting for its regrade batch', async () => {
    const store = prepareGlobalViewStore()
    store.alignment.criterion_revisions = [
      {
        revision_id: 'revision-1',
        case_id: 'case-a',
        criteria_file_name: 'rubric.json',
        criterion_id: '1',
        target_rubric_version: 'rubric-v2',
        target_criterion_hash: 'criterion-v2',
        status: 'awaiting_audit',
        report_names: ['report-a'],
        created_at: '2026-08-15T00:00:00Z',
        updated_at: '2026-08-15T00:00:00Z',
      },
    ]
    const refreshSpy = vi.spyOn(store, 'loadAlignment').mockResolvedValue(store.alignment)
    const harness = defineComponent({
      setup() {
        useAutoGradeBatchController()
        return () => h('div')
      },
    })

    const wrapper = mount(harness)

    await vi.waitFor(() => expect(refreshSpy).toHaveBeenCalledWith('case-a'))
    wrapper.unmount()
  })

  it('places Report/Global toggle in the app header and removes the middle-panel toggle', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const wrapper = mount(App)

    expect(wrapper.get('[data-testid="page-view-toggle"]').text()).toContain('Report View')
    expect(wrapper.get('[data-testid="page-view-toggle"]').text()).toContain('Global View')
    expect(wrapper.find('[data-testid="global-mode-toggle"]').exists()).toBe(false)
    await wrapper.get('[data-testid="page-view-toggle"] button:last-child').trigger('click')
    expect(store.mode).toBe('global')
    expect(wrapper.find('[data-testid="global-view"]').exists()).toBe(true)
  })

  it('allows Global View when only non-blocking grade confirmations remain', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.alignment.global_readiness = {
      allowed: false,
      blocker_count: 1,
      blockers: [
        {
          kind: 'point_allocation',
          label: 'Confirm scoring-point allocation',
          report_name: 'report-b',
          criterion_id: '1',
        },
      ],
    }
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const wrapper = mount(App)

    await wrapper.get('[data-testid="page-view-toggle"] button:last-child').trigger('click')
    expect(store.mode).toBe('global')
    expect(wrapper.find('[data-testid="global-readiness-modal"]').exists()).toBe(false)
  })

  it('blocks Global View for an open conflict and navigates to it', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.alignment.open_conflicts = [
      {
        conflict_id: 'conflict-1',
        conflict_type: 'score_rubric',
        criteria_id: '1',
        reason: 'Score conflicts with the rubric.',
        open: true,
        suspect_events: [{ event_id: 'event-1', report_name: 'report-b', score: 2 }],
      },
    ]
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const wrapper = mount(App)

    await wrapper.get('[data-testid="page-view-toggle"] button:last-child').trigger('click')
    expect(store.mode).toBe('single')
    expect(wrapper.get('[data-testid="global-readiness-modal"]').text()).toContain(
      'Resolve grading conflict',
    )
    expect(wrapper.get('[data-testid="global-readiness-modal"]').text()).not.toContain(
      'Criterion 1',
    )
    await wrapper.get('[data-testid="global-readiness-modal"] button').trigger('click')
    expect(store.activeReportId).toBe('report-b')
    expect(store.activeCriteriaId).toBe('1')
  })

  it('blocks Global View while a scoring-standard proposal awaits confirmation', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    store.criterionProposals = [
      {
        proposal_id: 'proposal-1',
        case_id: 'case-a',
        criteria_file_name: 'rubric.json',
        proposal_type: 'update_criterion',
        status: 'pending',
        target_criterion_id: '1',
        suggested_criterion: {
          ...criterion('1', 'Updated evidence', 10),
          scoring_points: [
            {
              point_id: '1.p1',
              summary: 'Updated evidence point',
              deduction_rules: [
                {
                  rule_id: '1.p1.missing',
                  condition: 'Updated evidence is missing.',
                  deduct: 10,
                },
              ],
              max_deduction: 10,
              aggregation: 'max_triggered',
            },
          ],
          scoring_aggregation: { mode: 'sum_deductions_capped', cap: 10 },
        },
        rationale: 'Refine the scoring standard.',
        source_implicit_grade_ids: [],
        source_grade_digests: [],
        source_event_ids: ['event-1'],
        source_grade_assignments: [],
        confirmed_event_ids: [],
        proposal_version: 1,
        created_at: '2026-08-12T00:00:00Z',
        updated_at: '2026-08-12T00:00:00Z',
      },
    ]
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const confirmProposalSpy = vi
      .spyOn(store, 'confirmCriterionProposal')
      .mockImplementation(async (_proposal, _suggested, _target, options) => {
        options?.onPersisted?.(null)
        return null
      })
    const wrapper = mount(App)

    await wrapper.get('[data-testid="page-view-toggle"] button:last-child').trigger('click')
    expect(store.mode).toBe('single')
    expect(wrapper.get('[data-testid="global-readiness-modal"]').text()).toContain(
      'Review scoring standard update',
    )
    expect(wrapper.get('[data-testid="global-readiness-modal"]').text()).not.toContain(
      'Criterion 1',
    )
    await wrapper.get('[data-testid="global-readiness-modal"] button').trigger('click')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="proposal-review-modal"]').exists()).toBe(true),
    )
    const modal = wrapper.get('[data-testid="proposal-review-modal"]')
    const confirmButton = modal
      .findAll('button')
      .find((button) => button.text().includes('Confirm standard'))
    expect(confirmButton).toBeDefined()
    await confirmButton!.trigger('click')
    await vi.waitFor(() => expect(confirmProposalSpy).toHaveBeenCalledOnce())
    expect(confirmProposalSpy.mock.calls[0]?.[2]).toBeUndefined()
    expect(confirmProposalSpy.mock.calls[0]?.[3]?.onPersisted).toBeTypeOf('function')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="proposal-review-modal"]').exists()).toBe(false),
    )
    wrapper.unmount()
  })

  it('leaves Global View if a new human-required blocker appears', async () => {
    const store = prepareGlobalViewStore()
    store.cases = []
    vi.spyOn(store, 'loadCases').mockResolvedValue(undefined)
    const wrapper = mount(App)
    await wrapper.get('[data-testid="page-view-toggle"] button:last-child').trigger('click')
    store.setGlobalFocusedReport('report-a')
    store.alignment = {
      ...store.alignment,
      global_readiness: {
        allowed: false,
        blocker_count: 1,
        blockers: [
          {
            kind: 'repair_required',
            label: 'Confirm scoring repair',
            criterion_id: '1',
          },
        ],
      },
    }
    await wrapper.vm.$nextTick()
    expect(store.mode).toBe('single')
    expect(store.globalFocusedReportId).toBeNull()
    expect(wrapper.find('[data-testid="global-readiness-modal"]').exists()).toBe(true)
  })

  it('keeps Global View read-only by suppressing hidden Report View auto-grading', async () => {
    const store = prepareGlobalViewStore()
    store.alignment.criterion_authorizations = [
      {
        criterion_id: '1',
        criteria_file_name: 'rubric.json',
        state: 'batch_authorized',
      },
    ]
    store.alignment.criterion_global_summaries[0]!.eligible_report_names = ['report-c']
    store.alignment.criterion_global_summaries[0]!.eligible_count = 1
    const autoGrade = vi.spyOn(api, 'autoGradeCaseReport')

    store.toggleMode('global')
    await store.recoverAutoGradesForCase()
    await Promise.resolve()

    expect(autoGrade).not.toHaveBeenCalled()
  })
})
