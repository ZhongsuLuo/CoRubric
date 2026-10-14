import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api } from '../api/client'
import { useGradingStore } from './grading'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(api, 'listCriteria').mockResolvedValue({
    status: 'ok',
    data: { items: ['new-case.json'] },
  })
  vi.spyOn(api, 'getCriteria').mockResolvedValue({
    status: 'ok',
    data: {
      item: {
        schema_version: 'structured-rubric-v1',
        title: '新标准',
        language: 'zh',
        background: '',
        base_score: 100,
        criteria: [],
      },
    },
  })
  vi.spyOn(api, 'getCaseAlignment').mockResolvedValue({ status: 'ok', data: {} })
  vi.spyOn(api, 'listCaseReports').mockResolvedValue({ status: 'ok', data: { items: [] } })
  vi.spyOn(api, 'listImplicitGrades').mockResolvedValue({ status: 'ok', data: { items: [] } })
  vi.spyOn(api, 'listCriterionProposals').mockResolvedValue({ status: 'ok', data: { items: [] } })
})

it('loads an uploaded case with its own rubric instead of retaining the previous case rubric', async () => {
  const store = useGradingStore()
  store.activeCaseId = '新case'
  store.activeCriteriaFile = 'previous.json'
  store.startNewCriteria({ title: '旧标准' })
  await store.loadCase('新case')
  expect(api.listCriteria).toHaveBeenCalledWith('新case')
  expect(api.getCriteria).toHaveBeenCalledWith('new-case.json')
  expect(store.activeCriteriaFile).toBe('new-case.json')
  expect(store.criteriaSchema?.title).toBe('新标准')
})

it('uses the server-assigned case-specific filename when creating a rubric', async () => {
  const store = useGradingStore()
  store.activeCaseId = '新case'
  store.startNewCriteria({ title: '评分标准' })
  vi.spyOn(api, 'saveCriteria').mockResolvedValue({
    status: 'ok',
    data: { path: '/tmp/x', file_name: 'isolated.json' },
  })
  await store.saveCriteriaFile('评分标准.json')
  expect(api.saveCriteria).toHaveBeenCalledWith('评分标准.json', expect.any(Object), '新case')
  expect(store.activeCriteriaFile).toBe('isolated.json')
})

it('uses the catalog default when a case name matches a demo but its rubric differs', async () => {
  const store = useGradingStore()
  store.activeCaseId = 'case 1'
  await store.loadCase('case 1')
  expect(api.getCriteria).toHaveBeenCalledWith('new-case.json')
  expect(api.getCriteria).not.toHaveBeenCalledWith('Case 1 评分标准.json')
})
