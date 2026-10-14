import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { useGradingStore } from '../../stores/grading'
import EvidenceRelationOverlay from './EvidenceRelationOverlay.vue'

const rect = (left: number, top: number, width: number, height: number): DOMRect =>
  ({
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    x: left,
    y: top,
    toJSON: () => ({}),
  }) as DOMRect

const Host = defineComponent({
  components: { EvidenceRelationOverlay },
  template: `
    <div data-testid="report-view-layout">
      <div data-testid="report-scroll-container">
        <div class="markdown-body">
          <mark data-testid="highlight-1" data-highlight-id="evidence-1">first highlight</mark>
          <mark data-testid="highlight-2" data-highlight-id="evidence-2">second highlight</mark>
        </div>
        <nav data-testid="evidence-map">
          <button data-testid="marker-1" data-highlight-id="evidence-1">first marker</button>
          <button data-testid="marker-2" data-highlight-id="evidence-2">second marker</button>
        </nav>
      </div>
      <div data-testid="criteria-list">
        <div data-testid="assessment-table-header">header</div>
        <div>
          <span
            data-testid="port-1"
            data-criterion-id="criterion-1"
            data-criterion-connection-highlight-id="evidence-1"
            data-connection-color="green"
          />
          <span
            data-testid="port-2"
            data-criterion-id="criterion-2"
            data-criterion-connection-highlight-id="evidence-2"
            data-connection-color="amber"
          />
        </div>
      </div>
      <EvidenceRelationOverlay />
    </div>
  `,
})

const placeRelationElements = (wrapper: ReturnType<typeof mount>) => {
  wrapper.get('[data-testid="report-view-layout"]').element.getBoundingClientRect = () =>
    rect(0, 0, 1000, 600)
  wrapper.get('[data-testid="report-scroll-container"]').element.getBoundingClientRect = () =>
    rect(0, 0, 400, 600)
  wrapper.get('.markdown-body').element.getBoundingClientRect = () => rect(40, 0, 340, 600)
  wrapper.get('[data-testid="criteria-list"]').element.getBoundingClientRect = () =>
    rect(500, 80, 500, 520)
  wrapper.get('[data-testid="assessment-table-header"]').element.getBoundingClientRect = () =>
    rect(500, 80, 500, 40)
  wrapper.get('[data-testid="port-1"]').element.getBoundingClientRect = () => rect(700, 200, 8, 8)
  wrapper.get('[data-testid="port-2"]').element.getBoundingClientRect = () => rect(700, 350, 8, 8)
  wrapper.get('[data-testid="marker-1"]').element.getBoundingClientRect = () =>
    rect(390, 110, 10, 10)
  wrapper.get('[data-testid="marker-2"]').element.getBoundingClientRect = () =>
    rect(390, 310, 10, 10)
  wrapper.get('[data-testid="highlight-1"]').element.getBoundingClientRect = () =>
    rect(100, 120, 180, 20)
  wrapper.get('[data-testid="highlight-2"]').element.getBoundingClientRect = () =>
    rect(120, 320, 160, 20)
}

beforeEach(() => setActivePinia(createPinia()))

describe('EvidenceRelationOverlay', () => {
  it('draws criterion-to-map and map-to-highlight paths for selected and unselected criteria', async () => {
    const store = useGradingStore()
    store.activeCriteriaId = 'criterion-1'
    store.activeHighlightId = 'evidence-1'
    const wrapper = mount(Host, { attachTo: document.body })
    placeRelationElements(wrapper)

    window.dispatchEvent(new Event('resize'))
    await new Promise((resolve) => window.setTimeout(resolve, 30))

    const overlay = wrapper.get('[data-testid="evidence-relation-overlay"]')
    expect(overlay.findAll('[data-relation-stage="criterion-map"]')).toHaveLength(2)
    expect(overlay.findAll('[data-relation-stage="map-highlight"]')).toHaveLength(2)
    expect(overlay.findAll('[data-relation-active="true"]')).toHaveLength(2)
    expect(overlay.findAll('[data-relation-active="false"]')).toHaveLength(2)
    expect(overlay.find('[stroke="var(--source-model)"]').exists()).toBe(true)
    expect(overlay.find('[stroke="var(--status-review)"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('routes through the report margin to the nearest rendered line-fragment border', async () => {
    const wrapper = mount(Host, { attachTo: document.body })
    placeRelationElements(wrapper)
    wrapper.get('[data-testid="marker-1"]').element.getBoundingClientRect = () =>
      rect(390, 300, 10, 10)
    const highlight = wrapper.get('[data-testid="highlight-1"]').element as HTMLElement
    highlight.getBoundingClientRect = () => rect(100, 120, 180, 200)
    highlight.getClientRects = () =>
      [rect(100, 120, 180, 20), rect(100, 300, 100, 20)] as unknown as DOMRectList

    window.dispatchEvent(new Event('resize'))
    await new Promise((resolve) => window.setTimeout(resolve, 30))

    const path = wrapper.get('[data-relation-stage="map-highlight"][stroke="var(--source-model)"]')
    const route = path.attributes('d') || ''
    expect(route.match(/ C /g)).toHaveLength(3)
    expect(route).not.toMatch(/[HLV]/)
    expect(route).toMatch(/200 300$/)
    wrapper.unmount()
  })

  it('draws nothing when a criterion has no matching in-situ marker', async () => {
    const wrapper = mount(Host)
    wrapper.get('[data-testid="marker-1"]').element.remove()
    wrapper.get('[data-testid="marker-2"]').element.remove()

    window.dispatchEvent(new Event('resize'))
    await new Promise((resolve) => window.setTimeout(resolve, 30))

    expect(wrapper.find('[data-testid="evidence-relation-overlay"] path').exists()).toBe(false)
  })

  it('removes a relation as soon as its criterion port leaves the table viewport', async () => {
    const wrapper = mount(Host, { attachTo: document.body })
    placeRelationElements(wrapper)
    wrapper.get('[data-testid="port-1"]').element.getBoundingClientRect = () => rect(700, 100, 8, 8)
    wrapper.get('[data-testid="port-2"]').element.getBoundingClientRect = () => rect(700, 60, 8, 8)

    document.dispatchEvent(new Event('scroll'))
    await new Promise((resolve) => window.setTimeout(resolve, 30))

    expect(wrapper.find('[data-testid="evidence-relation-overlay"] path').exists()).toBe(false)
    wrapper.unmount()
  })
})
