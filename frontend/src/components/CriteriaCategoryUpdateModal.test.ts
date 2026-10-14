import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CriteriaCategoryUpdateModal from './CriteriaCategoryUpdateModal.vue'
import type { CriteriaCategoryPreview } from '../types/schema'

const preview: CriteriaCategoryPreview = {
  source_content_hash: 'source-hash',
  rationale: 'Organized by grading dimensions.',
  categories: [
    { section: 'Completeness', count: 2 },
    { section: 'Data analysis', count: 1 },
  ],
  assignments: [
    {
      criteria_id: '1',
      criteria_name: 'Title',
      old_section: 'Introduction',
      section: 'Completeness',
    },
    {
      criteria_id: '2',
      criteria_name: 'Diagram',
      old_section: 'Principle',
      section: 'Completeness',
    },
    {
      criteria_id: '3',
      criteria_name: 'Calculation',
      old_section: 'Results',
      section: 'Data analysis',
    },
  ],
}

describe('CriteriaCategoryUpdateModal', () => {
  it('supports category-wide renaming, criterion movement, and confirmed assignments', async () => {
    const wrapper = mount(CriteriaCategoryUpdateModal, { props: { preview } })
    const categoryInputs = wrapper.findAll('input[aria-label="Category name"]')
    const categorySelects = wrapper.findAll('select[aria-label="Proposed category"]')

    await categoryInputs[0]!.setValue('Report completeness')
    await categorySelects[2]!.setValue('category-1')
    await wrapper.get('[data-testid="apply-criteria-categories"]').trigger('click')

    expect(wrapper.emitted('confirm')).toEqual([
      [
        [
          { criteria_id: '1', section: 'Report completeness' },
          { criteria_id: '2', section: 'Report completeness' },
          { criteria_id: '3', section: 'Report completeness' },
        ],
      ],
    ])
  })

  it('does not confirm while a category name is empty', async () => {
    const wrapper = mount(CriteriaCategoryUpdateModal, { props: { preview } })
    await wrapper.findAll('input[aria-label="Category name"]')[0]!.setValue('')
    await wrapper.get('[data-testid="apply-criteria-categories"]').trigger('click')

    expect(wrapper.emitted('confirm')).toBeUndefined()
    expect(wrapper.text()).toContain('Every category needs a name')
  })
})
