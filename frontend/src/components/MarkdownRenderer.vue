<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { HighlightItem } from '../types/schema'
import {
  applyMarkdownHighlights,
  normalizeSelectedText,
  syncMarkdownHighlightActiveState,
} from '../utils/markdownHighlight'
import { createMarkdownRenderer } from '../utils/markdownMath'
import { translate as t } from '../i18n'
import { evidenceColorClass } from '../utils/reportAssessmentPresentation'

const MAX_RENDER_CACHE_ENTRIES = 6
const renderedDocumentCache = new Map<string, string>()

const cacheRenderedDocument = (key: string, html: string) => {
  renderedDocumentCache.delete(key)
  renderedDocumentCache.set(key, html)
  while (renderedDocumentCache.size > MAX_RENDER_CACHE_ENTRIES) {
    const oldestKey = renderedDocumentCache.keys().next().value
    if (oldestKey === undefined) break
    renderedDocumentCache.delete(oldestKey)
  }
}

const props = withDefaults(
  defineProps<{
    markdown: string
    highlights: HighlightItem[]
    activeHighlightId: string | null
    activeHighlightIds?: string[]
    showEvidenceMap?: boolean
  }>(),
  { activeHighlightIds: () => [], showEvidenceMap: false },
)
const emit = defineEmits<{
  (e: 'select', payload: { text: string; x: number; y: number }): void
  (e: 'highlightClick', id: string): void
  (e: 'highlightContext', payload: { id: string; x: number; y: number }): void
}>()

const containerRef = ref<HTMLDivElement | null>(null)
const evidenceMarkers = ref<
  Array<{
    id: string
    top: number
    height: number
    label: string
    color: HighlightItem['color']
  }>
>([])
const evidenceMapHeight = ref(0)
const evidenceMapRef = ref<HTMLElement | null>(null)
const evidenceScrollThumb = ref({ top: 0, height: 100, position: 0 })
const apiBase = import.meta.env.VITE_API_BASE || '/api'
const md = createMarkdownRenderer()
let renderFrame: number | null = null
let renderVersion = 0
let completedRenderVersion = 0
let renderWaiters: Array<() => void> = []
let resizeObserver: ResizeObserver | null = null
let reportScrollerElement: HTMLElement | null = null
let thumbDrag: { startY: number; startScrollTop: number } | null = null

const renderedHtml = computed(() => md.render(props.markdown))
const highlightRenderKey = computed(() =>
  props.highlights
    .map((highlight) =>
      [
        highlight.id,
        highlight.criteria_id,
        highlight.color,
        highlight.score,
        highlight.max_score ?? '',
        highlight.anchor.text,
        highlight.anchor.occurrence ?? 0,
      ].join('\u001f'),
    )
    .join('\u001e'),
)
const renderedDocumentCacheKey = computed(
  () => `${props.markdown}\u001d${highlightRenderKey.value}\u001d${apiBase}`,
)
const activeHighlightIdSet = computed(
  () =>
    new Set([
      ...(props.activeHighlightId ? [props.activeHighlightId] : []),
      ...props.activeHighlightIds,
    ]),
)

const reportScrollMetrics = (scroller: HTMLElement) => {
  const viewportHeight = Math.max(scroller.clientHeight, 1)
  const documentHeight = Math.max(scroller.scrollHeight, viewportHeight)
  return {
    viewportHeight,
    documentHeight,
    maxScroll: Math.max(documentHeight - viewportHeight, 0),
  }
}

const updateEvidenceScrollThumb = () => {
  const scroller = reportScrollerElement
  if (!scroller) {
    evidenceScrollThumb.value = { top: 0, height: 100, position: 0 }
    return
  }

  const { viewportHeight, documentHeight, maxScroll } = reportScrollMetrics(scroller)
  const height = Math.max(8, Math.min(100, (viewportHeight / documentHeight) * 100))
  const position = maxScroll > 0 ? Math.max(0, Math.min(1, scroller.scrollTop / maxScroll)) : 0
  evidenceScrollThumb.value = {
    top: position * (100 - height),
    height,
    position: position * 100,
  }
}

const updateEvidenceMarkers = () => {
  const container = containerRef.value
  if (!container || !props.showEvidenceMap) {
    evidenceMarkers.value = []
    return
  }

  const containerRect = container.getBoundingClientRect()
  const documentHeight = Math.max(container.scrollHeight, 1)
  evidenceMapHeight.value = Math.max((reportScrollerElement?.clientHeight || 0) - 24, 160)
  updateEvidenceScrollThumb()
  const byId = new Map(props.highlights.map((highlight) => [highlight.id, highlight]))
  const elementsById = new Map<string, HTMLElement[]>()
  Array.from(
    container.querySelectorAll<HTMLElement>('[data-highlight-id]:not([data-highlight-badge])'),
  ).forEach((element) => {
    const id = element.dataset.highlightId || ''
    const highlight = byId.get(id)
    if (!highlight || id === '__pending_selection__') return
    const elements = elementsById.get(id) || []
    elements.push(element)
    elementsById.set(id, elements)
  })

  evidenceMarkers.value = Array.from(elementsById.entries()).flatMap(([id, elements]) => {
    const highlight = byId.get(id)
    if (!highlight || !elements.length) return []
    const rects = elements.map((element) => element.getBoundingClientRect())
    const firstTop = Math.min(...rects.map((rect) => rect.top)) - containerRect.top
    const lastBottom = Math.max(...rects.map((rect) => rect.bottom)) - containerRect.top
    const rawHeight = ((lastBottom - firstTop) / documentHeight) * 100
    const height = Math.max(0.8, Math.min(18, rawHeight))
    const top = Math.max(0.4, Math.min(99.6 - height, (firstTop / documentHeight) * 100))
    const maxScore = Number(highlight.max_score)
    const label = Number.isFinite(maxScore)
      ? t('Evidence: {score}/{maxScore}', { score: highlight.score, maxScore })
      : t('Evidence anchor')
    return [{ id, top, height, label, color: highlight.color }]
  })
}

const renderContent = () => {
  renderFrame = null
  const container = containerRef.value
  if (container) {
    const cacheKey = renderedDocumentCacheKey.value
    const cachedHtml = renderedDocumentCache.get(cacheKey)
    if (cachedHtml !== undefined) {
      container.innerHTML = cachedHtml
      renderedDocumentCache.delete(cacheKey)
      renderedDocumentCache.set(cacheKey, cachedHtml)
    } else {
      container.innerHTML = renderedHtml.value
      applyMarkdownHighlights(container, props.highlights, null, apiBase)
      cacheRenderedDocument(cacheKey, container.innerHTML)
    }
    syncMarkdownHighlightActiveState(
      container,
      props.highlights,
      props.activeHighlightId,
      props.activeHighlightIds,
    )
    updateEvidenceMarkers()
  }
  completedRenderVersion = renderVersion
  const waiters = renderWaiters
  renderWaiters = []
  waiters.forEach((resolve) => resolve())
}

const scheduleRender = () => {
  renderVersion += 1
  if (renderFrame !== null) return
  renderFrame = window.requestAnimationFrame(renderContent)
}

const waitForRender = async () => {
  await nextTick()
  if (renderFrame === null && completedRenderVersion >= renderVersion) return
  await new Promise<void>((resolve) => renderWaiters.push(resolve))
}

const syncActiveHighlight = () => {
  const container = containerRef.value
  if (container) {
    syncMarkdownHighlightActiveState(
      container,
      props.highlights,
      props.activeHighlightId,
      props.activeHighlightIds,
    )
  }
}

const onMouseUp = () => {
  setTimeout(() => {
    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0) return
    const text = normalizeSelectedText(selection.toString())
    if (!text) return
    const rect = selection.getRangeAt(0).getBoundingClientRect()
    // A range's right edge can be far from the actual text when it spans
    // several lines. Anchor the scoring popup at the selection's left-bottom
    // corner so it stays visually adjacent to the marked evidence.
    emit('select', { text, x: rect.left, y: rect.bottom || rect.top })
    selection.removeAllRanges()
  })
}

const onClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement | null
  if (!target) return
  const highlightId = target.closest<HTMLElement>('[data-highlight-id]')?.dataset.highlightId
  if (highlightId) {
    emit('highlightClick', highlightId)
    return
  }
  const imgEl = target.closest('img') as HTMLImageElement | null
  if (imgEl) {
    const alt = (imgEl.getAttribute('alt') || '').trim()
    const title = (imgEl.getAttribute('title') || '').trim()
    const src = imgEl.getAttribute('src') || ''
    const text = alt || title || src || '[image]'
    emit('select', { text, x: event.clientX, y: event.clientY })
    return
  }
}

const onContextMenu = (event: MouseEvent) => {
  const target = event.target as HTMLElement | null
  if (!target) return
  const highlightId = target.closest<HTMLElement>('[data-highlight-id]')?.dataset.highlightId
  if (highlightId) {
    event.preventDefault()
    emit('highlightContext', { id: highlightId, x: event.clientX, y: event.clientY })
  }
}

const scrollReportToTrackPosition = (clientY: number, behavior: ScrollBehavior) => {
  const scroller = reportScrollerElement
  const track = evidenceMapRef.value
  if (!scroller || !track) return
  const trackRect = track.getBoundingClientRect()
  if (trackRect.height <= 0) return
  const { viewportHeight, documentHeight } = reportScrollMetrics(scroller)
  const ratio = Math.max(0, Math.min(1, (clientY - trackRect.top) / trackRect.height))
  const target = ratio * documentHeight - viewportHeight / 2
  scroller.scrollTo({ top: Math.max(0, target), behavior })
}

const onEvidenceTrackPointerDown = (event: PointerEvent) => {
  if (event.button !== 0) return
  scrollReportToTrackPosition(event.clientY, 'smooth')
}

const onEvidenceThumbPointerDown = (event: PointerEvent) => {
  if (event.button !== 0 || !reportScrollerElement) return
  event.preventDefault()
  thumbDrag = {
    startY: event.clientY,
    startScrollTop: reportScrollerElement.scrollTop,
  }
}

const onEvidenceThumbPointerMove = (event: PointerEvent) => {
  const scroller = reportScrollerElement
  const track = evidenceMapRef.value
  if (!thumbDrag || !scroller || !track) return
  const { maxScroll } = reportScrollMetrics(scroller)
  const availableTrack = track.clientHeight * (1 - evidenceScrollThumb.value.height / 100)
  if (maxScroll <= 0 || availableTrack <= 0) return
  const scrollDelta = ((event.clientY - thumbDrag.startY) / availableTrack) * maxScroll
  scroller.scrollTop = Math.max(0, Math.min(maxScroll, thumbDrag.startScrollTop + scrollDelta))
  updateEvidenceScrollThumb()
}

const stopEvidenceThumbDrag = () => {
  thumbDrag = null
}

const onEvidenceThumbKeydown = (event: KeyboardEvent) => {
  const scroller = reportScrollerElement
  if (!scroller) return
  const { viewportHeight, documentHeight } = reportScrollMetrics(scroller)
  const page = Math.max(viewportHeight * 0.85, 40)
  const commands: Record<string, number> = {
    ArrowUp: scroller.scrollTop - 40,
    ArrowDown: scroller.scrollTop + 40,
    PageUp: scroller.scrollTop - page,
    PageDown: scroller.scrollTop + page,
    Home: 0,
    End: documentHeight,
  }
  const target = commands[event.key]
  if (target === undefined) return
  event.preventDefault()
  scroller.scrollTo({ top: target, behavior: 'smooth' })
}

watch(renderedHtml, scheduleRender)

watch(highlightRenderKey, () => {
  scheduleRender()
})

watch(
  () => [props.activeHighlightId, props.activeHighlightIds.join('\u001f')],
  () => {
    syncActiveHighlight()
  },
)

onMounted(() => {
  reportScrollerElement =
    containerRef.value?.closest<HTMLElement>('[data-testid="report-scroll-container"]') || null
  renderVersion += 1
  renderContent()
  if (typeof ResizeObserver !== 'undefined' && containerRef.value) {
    resizeObserver = new ResizeObserver(updateEvidenceMarkers)
    resizeObserver.observe(containerRef.value)
    if (reportScrollerElement) resizeObserver.observe(reportScrollerElement)
  }
  reportScrollerElement?.addEventListener('scroll', updateEvidenceScrollThumb, { passive: true })
  window.addEventListener('pointermove', onEvidenceThumbPointerMove)
  window.addEventListener('pointerup', stopEvidenceThumbDrag)
  window.addEventListener('pointercancel', stopEvidenceThumbDrag)
})

onBeforeUnmount(() => {
  if (renderFrame !== null) {
    window.cancelAnimationFrame(renderFrame)
  }
  const waiters = renderWaiters
  renderWaiters = []
  waiters.forEach((resolve) => resolve())
  resizeObserver?.disconnect()
  resizeObserver = null
  reportScrollerElement?.removeEventListener('scroll', updateEvidenceScrollThumb)
  reportScrollerElement = null
  window.removeEventListener('pointermove', onEvidenceThumbPointerMove)
  window.removeEventListener('pointerup', stopEvidenceThumbDrag)
  window.removeEventListener('pointercancel', stopEvidenceThumbDrag)
})

const renderedHighlightElement = (highlightId: string) => {
  const container = containerRef.value
  if (!container) return null
  return (
    Array.from(container.querySelectorAll<HTMLElement>('[data-highlight-id]')).find(
      (element) => element.dataset.highlightId === highlightId,
    ) || null
  )
}

const scrollToFirstHighlight = async (highlightIds: string[]) => {
  await waitForRender()

  for (const highlightId of highlightIds) {
    const element = renderedHighlightElement(highlightId)
    if (!element) continue
    element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    return highlightId
  }
  return null
}

const scrollToHighlight = async (highlightId: string) =>
  Boolean(await scrollToFirstHighlight([highlightId]))

const selectEvidenceMarker = (highlightId: string) => {
  emit('highlightClick', highlightId)
  void scrollToHighlight(highlightId)
}

defineExpose({ scrollToHighlight, scrollToFirstHighlight, waitForRender })
</script>

<template>
  <div
    class="grid min-h-full items-start gap-5"
    :class="props.showEvidenceMap ? 'grid-cols-[minmax(0,1fr)_14px]' : 'grid-cols-1'"
    @mouseup="onMouseUp"
    @click="onClick"
    @contextmenu="onContextMenu"
  >
    <div ref="containerRef" class="markdown-body min-w-0 text-sm leading-6"></div>
    <nav
      v-if="props.showEvidenceMap"
      ref="evidenceMapRef"
      class="sticky top-3 -mt-3 min-h-40 self-start overflow-visible rounded-[4px] bg-slate-100 ring-1 ring-inset ring-slate-200"
      :style="{ height: `${evidenceMapHeight}px` }"
      :aria-label="t('Evidence map')"
      data-testid="evidence-map"
      @pointerdown.self="onEvidenceTrackPointerDown"
    >
      <button
        type="button"
        role="scrollbar"
        aria-orientation="vertical"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="Math.round(evidenceScrollThumb.position)"
        class="absolute left-px right-px z-0 min-h-6 cursor-grab rounded-[3px] border border-slate-400/60 bg-slate-400/20 shadow-sm transition-colors hover:bg-slate-400/30 focus:outline-none focus:ring-2 focus:ring-slate-400/40 active:cursor-grabbing"
        :style="{
          top: `${evidenceScrollThumb.top}%`,
          height: `${evidenceScrollThumb.height}%`,
        }"
        :aria-label="t('Report scroll position')"
        data-testid="evidence-scroll-thumb"
        @pointerdown.stop="onEvidenceThumbPointerDown"
        @keydown="onEvidenceThumbKeydown"
      />
      <button
        v-for="marker in evidenceMarkers"
        :key="marker.id"
        type="button"
        class="absolute left-[2px] right-[2px] z-10 min-h-[3px] rounded-[1px] opacity-75 transition-[left,right,opacity,box-shadow] hover:left-0 hover:right-0 hover:opacity-100 focus:left-0 focus:right-0 focus:opacity-100 focus:outline-none"
        :class="[
          evidenceColorClass(marker.color),
          activeHighlightIdSet.has(marker.id)
            ? 'left-0 right-0 z-10 opacity-100 shadow-[0_0_0_1px_rgba(15,23,42,0.3)]'
            : '',
        ]"
        :style="{ top: `${marker.top}%`, height: `${marker.height}%` }"
        :aria-label="marker.label"
        :title="marker.label"
        :data-highlight-id="marker.id"
        @click.stop="selectEvidenceMarker(marker.id)"
      ></button>
    </nav>
  </div>
</template>
