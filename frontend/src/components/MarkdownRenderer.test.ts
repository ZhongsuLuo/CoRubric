import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import type { HighlightItem } from '../types/schema'

const highlight: HighlightItem = {
  id: 'evidence-1',
  report_id: 'report-1',
  criteria_id: 'criterion-1',
  score: 7,
  max_score: 10,
  reason: 'Grounded evidence',
  source: 'llm',
  color: 'blue',
  anchor: { text: 'Exact evidence passage.', occurrence: 0 },
}

const ScrollerHost = defineComponent({
  components: { MarkdownRenderer },
  setup: () => ({ highlight }),
  template: `
    <div data-testid="report-scroll-container">
      <MarkdownRenderer
        markdown="Exact evidence passage."
        :highlights="[highlight]"
        :active-highlight-id="null"
        show-evidence-map
      />
    </div>
  `,
})

beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = vi.fn()
})

describe('MarkdownRenderer evidence map enhancement', () => {
  it('keeps report content and adds linked score/loss and position markers', async () => {
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown: '# Report\n\nExact evidence passage. More report content.',
        highlights: [highlight],
        activeHighlightId: null,
        showEvidenceMap: true,
      },
    })

    await wrapper.vm.waitForRender()

    expect(wrapper.get('.markdown-body').text()).toContain('Exact evidence passage.')
    expect(wrapper.get('[data-highlight-badge]').text()).toBe('7/10')
    expect(wrapper.get('[data-highlight-badge]').classes()).toContain('opacity-0')
    expect(wrapper.find('[data-testid="evidence-map"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="evidence-scroll-thumb"]').exists()).toBe(true)
    const marker = wrapper
      .get('[data-testid="evidence-map"]')
      .get('[data-highlight-id="evidence-1"]')
    expect(marker.classes()).toContain('rounded-[1px]')
    expect(marker.attributes('style')).toContain('height:')

    await marker.trigger('click')
    expect(wrapper.emitted('highlightClick')).toEqual([['evidence-1']])
  })

  it('uses the evidence rail as the report scrollbar', async () => {
    const wrapper = mount(ScrollerHost)
    const scroller = wrapper.get('[data-testid="report-scroll-container"]').element as HTMLElement
    Object.defineProperties(scroller, {
      clientHeight: { configurable: true, value: 200 },
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: { configurable: true, value: 400, writable: true },
    })
    scroller.scrollTo = vi.fn()
    scroller.dispatchEvent(new Event('scroll'))
    await wrapper.vm.$nextTick()

    const thumb = wrapper.get('[data-testid="evidence-scroll-thumb"]')
    expect(thumb.attributes('role')).toBe('scrollbar')
    expect(thumb.attributes('aria-valuenow')).toBe('50')
    expect(thumb.attributes('style')).toContain('height: 20%')

    const track = wrapper.get('[data-testid="evidence-map"]')
    track.element.getBoundingClientRect = () =>
      ({
        top: 0,
        bottom: 200,
        left: 0,
        right: 14,
        width: 14,
        height: 200,
      }) as DOMRect
    track.element.dispatchEvent(
      new MouseEvent('pointerdown', { bubbles: true, button: 0, clientY: 150 }),
    )
    await wrapper.vm.$nextTick()
    expect(scroller.scrollTo).toHaveBeenCalledWith({
      top: 650,
      behavior: 'smooth',
    })
  })

  it('reveals the score/loss label for the active evidence', async () => {
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown: 'Exact evidence passage.',
        highlights: [highlight],
        activeHighlightId: 'evidence-1',
        showEvidenceMap: true,
      },
    })

    await wrapper.vm.waitForRender()
    expect(wrapper.get('[data-highlight-badge]').attributes('style')).toContain('opacity: 1')
  })

  it('renders the legacy purple pending value with the teal interaction treatment', async () => {
    const pendingHighlight: HighlightItem = {
      ...highlight,
      id: 'pending-evidence',
      color: 'purple',
    }
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown: 'Exact evidence passage.',
        highlights: [pendingHighlight],
        activeHighlightId: 'pending-evidence',
      },
    })

    await wrapper.vm.waitForRender()
    const evidence = wrapper.get(
      '[data-highlight-id="pending-evidence"]:not([data-highlight-badge])',
    )
    expect(evidence.classes()).toContain('evidence-highlight-pending')
    expect(evidence.classes()).toContain('evidence-active-pending')
    expect(evidence.classes().some((className) => className.includes('purple'))).toBe(false)
  })

  it('emphasizes every evidence segment for the selected card while keeping one primary label', async () => {
    const secondHighlight: HighlightItem = {
      ...highlight,
      id: 'evidence-2',
      anchor: { text: 'Second evidence passage.', occurrence: 0 },
    }
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown: 'Exact evidence passage.\n\nUnrelated text.\n\nSecond evidence passage.',
        highlights: [highlight, secondHighlight],
        activeHighlightId: 'evidence-2',
        activeHighlightIds: ['evidence-1', 'evidence-2'],
        showEvidenceMap: true,
      },
    })

    await wrapper.vm.waitForRender()

    expect(
      wrapper.get('[data-highlight-id="evidence-1"]:not([data-highlight-badge])').classes(),
    ).toContain('evidence-active-teacher')
    expect(
      wrapper.get('[data-highlight-id="evidence-2"]:not([data-highlight-badge])').classes(),
    ).toContain('evidence-active-teacher')
    expect(
      wrapper.get('[data-highlight-badge][data-highlight-id="evidence-2"]').attributes('style'),
    ).toContain('opacity: 1')
    expect(
      wrapper.get('[data-highlight-badge][data-highlight-id="evidence-1"]').attributes('style') ||
        '',
    ).not.toContain('opacity: 1')
  })

  it('does not add a rail when the enhancement is disabled', async () => {
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown: 'Exact evidence passage.',
        highlights: [highlight],
        activeHighlightId: null,
      },
    })

    await wrapper.vm.waitForRender()
    expect(wrapper.find('[data-testid="evidence-map"]').exists()).toBe(false)
  })

  it('locates image evidence by source without changing text highlighting', async () => {
    const imageHighlight: HighlightItem = {
      ...highlight,
      id: 'image-evidence',
      anchor: {
        anchor_type: 'image',
        text: 'images/chart.png',
        image_src: 'images/chart.png',
        description: 'A long grading-only chart description.',
      },
    }
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown:
          'Exact evidence passage.\n\n![Chart alt](/case-files/case/markdown/report/images/chart.png)',
        highlights: [highlight, imageHighlight],
        activeHighlightId: 'image-evidence',
        showEvidenceMap: true,
      },
    })

    await wrapper.vm.waitForRender()
    expect(wrapper.get('img').attributes('data-highlight-id')).toBe('image-evidence')
    expect(wrapper.get('img').classes()).toContain('ring-4')
    expect(wrapper.get('[data-highlight-id="evidence-1"]')).toBeTruthy()
    expect(wrapper.text()).not.toContain('A long grading-only chart description.')
  })

  it('shows only the selected score and applies one color across nested evidence', async () => {
    const shortHighlight: HighlightItem = {
      ...highlight,
      id: 'short-evidence',
      criteria_id: 'short-criterion',
      color: 'purple',
      score: 2,
      max_score: 4,
      anchor: { text: 'First half', occurrence: 0 },
    }
    const fullHighlight: HighlightItem = {
      ...highlight,
      id: 'full-evidence',
      criteria_id: 'full-criterion',
      color: 'green',
      score: 4,
      max_score: 4,
      anchor: { text: 'First half and second half.', occurrence: 0 },
    }
    const wrapper = mount(MarkdownRenderer, {
      props: {
        markdown: 'First half and second half.',
        highlights: [shortHighlight, fullHighlight],
        activeHighlightId: 'full-evidence',
        showEvidenceMap: true,
      },
    })

    await wrapper.vm.waitForRender()

    const full = wrapper.get('[data-highlight-id="full-evidence"]:not([data-highlight-badge])')
    const nestedShort = full.get('[data-highlight-id="short-evidence"]:not([data-highlight-badge])')
    expect(full.classes()).toContain('evidence-active-model')
    expect(nestedShort.classes()).toContain('evidence-nested-model')
    expect(nestedShort.classes()).not.toContain('evidence-highlight-pending')
    expect(nestedShort.classes()).not.toContain('evidence-active-model')

    const fullBadge = wrapper.get('[data-highlight-badge][data-highlight-id="full-evidence"]')
    const shortBadge = wrapper.get('[data-highlight-badge][data-highlight-id="short-evidence"]')
    expect(fullBadge.attributes('style')).toContain('opacity: 1')
    expect(shortBadge.attributes('style') || '').not.toContain('opacity: 1')
    expect(shortBadge.classes()).not.toContain('group-hover/evidence:opacity-100')
  })
})
