import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ImageEvidencePreviewModal from './ImageEvidencePreviewModal.vue'

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ImageEvidencePreviewModal', () => {
  it('renders the full image and closes with Escape', async () => {
    const wrapper = mount(ImageEvidencePreviewModal, {
      attachTo: document.body,
      props: {
        src: '/api/case-files/case/report/images/chart.png',
        title: 'Report · Image evidence 1',
        alt: 'Calibration chart',
      },
    })

    const image = document.body.querySelector<HTMLImageElement>(
      '[data-testid="image-evidence-preview-image"]',
    )
    expect(image?.getAttribute('src')).toBe('/api/case-files/case/report/images/chart.png')
    expect(image?.getAttribute('alt')).toBe('Calibration chart')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('closes when the backdrop is clicked and shows an unavailable state', async () => {
    const wrapper = mount(ImageEvidencePreviewModal, {
      attachTo: document.body,
      props: { src: '', title: 'Missing image', alt: 'Missing' },
    })

    expect(
      document.body.querySelector('[data-testid="image-evidence-preview-error"]'),
    ).not.toBeNull()
    document.body.querySelector<HTMLElement>('[data-testid="image-evidence-preview"]')!.click()
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})
