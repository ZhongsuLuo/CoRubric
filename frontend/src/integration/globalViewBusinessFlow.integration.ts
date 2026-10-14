import { readFileSync } from 'node:fs'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import GlobalView from '../components/global-view/GlobalView.vue'
import { useGradingStore } from '../stores/grading'
import type { AlignmentState, CohortSynthesis, CriteriaSchema } from '../types/schema'

type Contract = {
  manifest: {
    case_id: string
    ready_case_id: string
    large_case_id: string
    scale_cases: Record<string, string>
  }
  criteria: CriteriaSchema
  responses: Record<string, AlignmentState>
  synthesis: CohortSynthesis
  persistence_hash_before: string
  persistence_hash_after: string
}

const contractPath = process.env.GLOBAL_QA_CONTRACT_PATH
if (!contractPath) {
  throw new Error('GLOBAL_QA_CONTRACT_PATH is required for the simulated frontend/backend gate')
}
const contract = JSON.parse(readFileSync(contractPath, 'utf-8')) as Contract

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
  class ResizeObserverMock {
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.scrollTo = vi.fn()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  })
  vi.spyOn(api, 'getCohortSynthesis').mockResolvedValue({
    status: 'ok',
    data: contract.synthesis,
  })
})

const loadContractCase = async (caseId: string) => {
  const store = useGradingStore()
  store.activeCaseId = caseId
  store.activeCriteriaFile = 'qa_global_conflict.json'
  store.criteriaSchema = contract.criteria
  const response = contract.responses[caseId]!
  const reports = response.global_report_summaries || []
  store.reports = reports.map((item) => ({
    id: item.report_id,
    name: item.report_name,
    markdown: '# QA report\n\n可定位的证据、计算过程和结论。教师保留业务判断。',
    conversion_status: 'finished',
  }))
  vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({
    status: 'ok',
    data: response,
  })
  vi.spyOn(api, 'autoGradeCaseReport').mockResolvedValue({
    status: 'ok',
    data: { grades: [], abstentions: [], failures: [], result: null },
  })
  await store.loadAlignment(caseId)
  return store
}

describe('deterministic backend-to-frontend Global View contract', () => {
  it('preserves authoritative loss, conflict, provenance and readiness fields', async () => {
    expect(contract.persistence_hash_after).toBe(contract.persistence_hash_before)
    expect(contract.synthesis.model_id).toBe('qa-deterministic-synthesis')
    expect(contract.synthesis.language).toBe('zh')
    expect(contract.synthesis.analysis).toContain('明显高于次高项')
    expect(contract.synthesis.analysis).not.toContain('整体问题并非均匀分布')
    expect(contract.synthesis.insights[0]?.summary).toBe('证据基本相关，但论证链不完整')
    expect(contract.synthesis.findings?.[0]?.insight_id).toBe('finding-1')
    const store = await loadContractCase(contract.manifest.ready_case_id)
    expect(store.alignment.global_readiness?.allowed).toBe(true)
    const criterion = store.getCriterionGlobalSummary('1')!
    expect(criterion.scored_count).toBe(5)
    expect(criterion.statistical_count).toBe(5)
    expect(criterion.average_loss).toBeCloseTo(1.8)
    expect(criterion.conflict_count).toBe(0)
    expect(criterion.teacher_count).toBe(3)
    expect(criterion.model_count).toBe(2)
    expect(criterion.point_loss_summaries?.[0]?.affected_report_count).toBe(4)

    const wrapper = mount(GlobalView)
    expect(wrapper.findAll('[data-testid="global-report-tile"]')).toHaveLength(8)
    expect(wrapper.findAll('[data-testid="global-criterion-card"]')).toHaveLength(0)
    expect(wrapper.find('[data-testid^="heatmap-score-dots-"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Grading incomplete')
  })

  it('keeps the blocking case out and carries exact confirmation targets', async () => {
    const store = await loadContractCase(contract.manifest.case_id)
    expect(store.alignment.global_readiness?.allowed).toBe(false)
    expect(store.alignment.global_readiness?.blockers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: 'repair_required',
          criterion_id: '3',
        }),
      ]),
    )
  })

  it('normalizes every report-count boundary without truncation', async () => {
    const cases = {
      ...contract.manifest.scale_cases,
      '51': contract.manifest.large_case_id,
    }
    for (const [count, caseId] of Object.entries(cases)) {
      const store = await loadContractCase(caseId)
      expect(store.alignment.global_report_summaries).toHaveLength(Number(count))
      expect(
        new Set(store.alignment.global_report_summaries?.map((item) => item.report_id)).size,
      ).toBe(Number(count))
    }
  })
})
