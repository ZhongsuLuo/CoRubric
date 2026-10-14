import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setLocale } from '../i18n'
import { api } from '../api/client'
import CaseFolderImport from './CaseFolderImport.vue'
import type { CaseImportJob } from '../types/imports'

const job = (status: CaseImportJob['status'] = 'running'): CaseImportJob => ({
  id: 'a'.repeat(32),
  case_id: '实验报告',
  engine: 'hybrid',
  status,
  created_at: '2026-10-08T10:00:00Z',
  updated_at: '2026-10-08T10:00:00Z',
  events: [],
  files: [
    {
      id: '0',
      path: '实验报告/a.pdf',
      report_name: 'a',
      pages: 2,
      pages_done: status === 'completed' ? 2 : 0,
      status: status === 'completed' ? 'completed' : 'running',
      stage: '图像增强',
      error: null,
    },
  ],
})
const folderFile = (name: string, path: string) => {
  const file = new File(['test'], name)
  Object.defineProperty(file, 'webkitRelativePath', { value: path })
  return file
}
let wrapper: ReturnType<typeof mount> | undefined
beforeEach(() => {
  setLocale('zh')
  vi.useFakeTimers()
  vi.spyOn(api, 'listCaseImports').mockResolvedValue({ status: 'ok', data: { items: [] } })
})
afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('folder import', () => {
  it('defaults to the folder name, filters PDFs, and submits the edited name with the hybrid parser', async () => {
    const upload = vi
      .spyOn(api, 'uploadCaseFolder')
      .mockResolvedValue({ status: 'ok', data: job() })
    wrapper = mount(CaseFolderImport, { global: { stubs: { teleport: true } } })
    await wrapper.get('[data-testid="folder-import-open"]').trigger('click')
    const input = wrapper.get('[data-testid="folder-input"]')
    const files = [
      folderFile('a.PDF', '实验报告/sub/a.PDF'),
      folderFile('readme.txt', '实验报告/readme.txt'),
    ]
    Object.defineProperty(input.element, 'files', { value: files })
    await input.trigger('change')
    expect((wrapper.get('[data-testid="import-name"]').element as HTMLInputElement).value).toBe(
      '实验报告',
    )
    await wrapper.get('[data-testid="import-name"]').setValue('新的case')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(upload).toHaveBeenCalledWith('新的case', [files[0]], 'hybrid')
    expect(wrapper.text()).toContain('图像增强')
  })

  it('restores persisted tasks and can select a completed case without starting scoring', async () => {
    vi.mocked(api.listCaseImports).mockResolvedValue({
      status: 'ok',
      data: { items: [job('completed')] },
    })
    wrapper = mount(CaseFolderImport, { global: { stubs: { teleport: true } } })
    await flushPromises()
    await wrapper.get('[data-testid="folder-import-open"]').trigger('click')
    const choose = wrapper.findAll('button').find((b) => b.text().includes('选择此case'))!
    await choose.trigger('click')
    expect(wrapper.emitted('use-case')).toEqual([['实验报告']])
  })

  it('keeps files and shows a conflict when the case name already exists', async () => {
    vi.spyOn(api, 'uploadCaseFolder').mockRejectedValue(new Error('{"detail":"已存在同名case"}'))
    wrapper = mount(CaseFolderImport, { global: { stubs: { teleport: true } } })
    await wrapper.get('[data-testid="folder-import-open"]').trigger('click')
    const input = wrapper.get('[data-testid="folder-input"]')
    Object.defineProperty(input.element, 'files', {
      value: [folderFile('a.pdf', '实验报告/a.pdf')],
    })
    await input.trigger('change')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('已存在同名case')
    expect(wrapper.get('[data-testid="import-submit"]').attributes('disabled')).toBeUndefined()
  })
})
