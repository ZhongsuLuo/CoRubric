<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useGradingStore } from '../../stores/grading'
import type { HighlightItem } from '../../types/schema'
import { evidenceStrokeColor } from '../../utils/reportAssessmentPresentation'

type RelationStage = 'criterion-map' | 'map-highlight'

type RelationPath = {
  key: string
  path: string
  color: string
  opacity: number
  strokeWidth: number
  stage: RelationStage
  active: boolean
}

type Bounds = Pick<DOMRect, 'top' | 'right' | 'bottom' | 'left'>

const store = useGradingStore()
const relations = ref<RelationPath[]>([])
let frame: number | null = null
let mutationObserver: MutationObserver | null = null
let resizeObserver: ResizeObserver | null = null

const exactAttributeElement = (root: ParentNode, attribute: string, id: string) =>
  Array.from(root.querySelectorAll<HTMLElement>(`[${attribute}]`)).find(
    (element) => element.getAttribute(attribute) === id,
  ) || null

const isVisibleWithin = (rect: DOMRect, bounds: Bounds) =>
  rect.width > 0 &&
  rect.height > 0 &&
  rect.bottom >= bounds.top &&
  rect.top <= bounds.bottom &&
  rect.right >= bounds.left &&
  rect.left <= bounds.right

const isFullyVisibleWithin = (rect: DOMRect, bounds: Bounds) =>
  rect.width > 0 &&
  rect.height > 0 &&
  rect.top >= bounds.top &&
  rect.bottom <= bounds.bottom &&
  rect.left >= bounds.left &&
  rect.right <= bounds.right

const connectionStyle = (activeEvidence: boolean, selectedCriterion: boolean) => {
  if (activeEvidence) return { criterionOpacity: 0.82, mapOpacity: 0.68, strokeWidth: 1.7 }
  if (selectedCriterion) return { criterionOpacity: 0.5, mapOpacity: 0.34, strokeWidth: 1.25 }
  return { criterionOpacity: 0.2, mapOpacity: 0.13, strokeWidth: 0.85 }
}

const pathBetween = (
  startX: number,
  startY: number,
  endX: number,
  endY: number,
  bendRatio: number,
) => {
  const distance = Math.max(24, Math.abs(startX - endX) * bendRatio)
  return `M ${startX} ${startY} C ${startX - distance} ${startY}, ${endX + distance} ${endY}, ${endX} ${endY}`
}

const pathThroughReportMargin = (
  startX: number,
  startY: number,
  marginX: number,
  endX: number,
  laneY: number,
  endY: number,
) => {
  const verticalDirection = Math.sign(laneY - startY) || 1
  const turnRadius = Math.min(8, Math.abs(startX - marginX) / 2, Math.abs(laneY - startY) / 2)
  const approachRadius = Math.min(12, Math.abs(marginX - endX) / 4)
  const entryTurnY = startY + verticalDirection * turnRadius
  const exitTurnY = laneY - verticalDirection * turnRadius
  const laneEntryX = marginX - turnRadius
  return [
    `M ${startX} ${startY}`,
    `C ${startX - turnRadius} ${startY}, ${marginX} ${startY}, ${marginX} ${entryTurnY}`,
    `C ${marginX} ${exitTurnY}, ${marginX} ${laneY}, ${laneEntryX} ${laneY}`,
    `C ${marginX - turnRadius * 2} ${laneY}, ${endX + approachRadius} ${laneY}, ${endX} ${endY}`,
  ].join(' ')
}

const highlightClientRects = (element: HTMLElement) => {
  const clientRects = Array.from(element.getClientRects()).filter(
    (rect) => rect.width > 0 && rect.height > 0,
  )
  return clientRects.length ? clientRects : [element.getBoundingClientRect()]
}

const visibleCenterY = (rect: DOMRect, bounds: Bounds) =>
  (Math.max(rect.top, bounds.top) + Math.min(rect.bottom, bounds.bottom)) / 2

const nearestVisibleHighlightAnchor = (
  elements: HTMLElement[],
  markerY: number,
  reportBounds: Bounds,
) => {
  const rect = elements
    .flatMap(highlightClientRects)
    .filter((candidate) => isVisibleWithin(candidate, reportBounds))
    .sort(
      (left, right) =>
        Math.abs(visibleCenterY(left, reportBounds) - markerY) -
        Math.abs(visibleCenterY(right, reportBounds) - markerY),
    )[0]
  if (!rect) return null
  const borderOptions = [
    rect.top >= reportBounds.top
      ? { y: rect.top, laneY: Math.max(reportBounds.top, rect.top - 3) }
      : null,
    rect.bottom <= reportBounds.bottom
      ? { y: rect.bottom, laneY: Math.min(reportBounds.bottom, rect.bottom + 3) }
      : null,
  ].filter((option): option is { y: number; laneY: number } => option !== null)
  const border = borderOptions.sort(
    (left, right) => Math.abs(left.laneY - markerY) - Math.abs(right.laneY - markerY),
  )[0]
  if (!border) return null
  return { x: rect.right, ...border }
}

const updateRelations = () => {
  frame = null
  const layout = document.querySelector<HTMLElement>('[data-testid="report-view-layout"]')
  const evidenceMap = layout?.querySelector<HTMLElement>('[data-testid="evidence-map"]')
  const reportScroller = layout?.querySelector<HTMLElement>(
    '[data-testid="report-scroll-container"]',
  )
  const criteriaScroller = layout?.querySelector<HTMLElement>('[data-testid="criteria-list"]')
  const tableHeader = layout?.querySelector<HTMLElement>('[data-testid="assessment-table-header"]')
  const reportContent = layout?.querySelector<HTMLElement>('.markdown-body')
  if (
    !layout ||
    !evidenceMap ||
    !reportScroller ||
    !criteriaScroller ||
    !tableHeader ||
    !reportContent
  ) {
    relations.value = []
    return
  }

  const reportRect = reportScroller.getBoundingClientRect()
  const reportContentRect = reportContent.getBoundingClientRect()
  const criteriaRect = criteriaScroller.getBoundingClientRect()
  const tableHeaderRect = tableHeader.getBoundingClientRect()
  const criterionViewport: Bounds = {
    top: Math.max(criteriaRect.top, tableHeaderRect.bottom),
    right: criteriaRect.right,
    bottom: criteriaRect.bottom,
    left: criteriaRect.left,
  }
  const nextRelations: RelationPath[] = []
  const ports = layout.querySelectorAll<HTMLElement>('[data-criterion-connection-highlight-id]')

  ports.forEach((port) => {
    const highlightId = port.dataset.criterionConnectionHighlightId || ''
    const criterionId = port.dataset.criterionId || ''
    const marker = exactAttributeElement(evidenceMap, 'data-highlight-id', highlightId)
    if (!highlightId || !marker) return

    const portRect = port.getBoundingClientRect()
    const markerRect = marker.getBoundingClientRect()
    if (
      !isFullyVisibleWithin(portRect, criterionViewport) ||
      !isVisibleWithin(markerRect, reportRect)
    )
      return

    const colorName = (port.dataset.connectionColor || 'blue') as HighlightItem['color']
    const color = evidenceStrokeColor(colorName)
    const activeEvidence = store.activeHighlightId === highlightId
    const selectedCriterion = String(store.activeCriteriaId || '') === criterionId
    const style = connectionStyle(activeEvidence, selectedCriterion)
    const portX = portRect.left
    const portY = portRect.top + portRect.height / 2
    const markerRightX = markerRect.right
    const markerY = markerRect.top + markerRect.height / 2

    nextRelations.push({
      key: `${highlightId}-criterion-map`,
      stage: 'criterion-map',
      path: pathBetween(portX, portY, markerRightX, markerY, 0.36),
      color,
      opacity: style.criterionOpacity,
      strokeWidth: style.strokeWidth,
      active: activeEvidence,
    })

    const highlightElements = Array.from(
      reportContent.querySelectorAll<HTMLElement>(
        '[data-highlight-id]:not([data-highlight-badge])',
      ),
    ).filter((element) => element.dataset.highlightId === highlightId)
    const highlightAnchor = nearestVisibleHighlightAnchor(highlightElements, markerY, reportRect)
    if (!highlightAnchor) return
    const reportMarginX = Math.min(markerRect.left - 4, reportContentRect.right + 8)

    nextRelations.push({
      key: `${highlightId}-map-highlight`,
      stage: 'map-highlight',
      path: pathThroughReportMargin(
        markerRect.left,
        markerY,
        reportMarginX,
        highlightAnchor.x,
        highlightAnchor.laneY,
        highlightAnchor.y,
      ),
      color,
      opacity: style.mapOpacity,
      strokeWidth: Math.max(0.7, style.strokeWidth - 0.2),
      active: activeEvidence,
    })
  })

  relations.value = nextRelations.sort((left, right) => Number(left.active) - Number(right.active))
}

const scheduleRelationUpdate = () => {
  if (frame !== null) return
  frame = window.requestAnimationFrame(updateRelations)
}

watch(
  () => [
    store.activeHighlightId,
    store.activeCriteriaId,
    store.activeReportId,
    store.reportHighlights.map((highlight) => `${highlight.id}:${highlight.color}`).join('|'),
  ],
  () => void nextTick(scheduleRelationUpdate),
)

onMounted(() => {
  const layout = document.querySelector<HTMLElement>('[data-testid="report-view-layout"]')
  if (!layout) return
  mutationObserver = new MutationObserver(scheduleRelationUpdate)
  mutationObserver.observe(layout, { childList: true, subtree: true })
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(scheduleRelationUpdate)
    resizeObserver.observe(layout)
  }
  document.addEventListener('scroll', scheduleRelationUpdate, true)
  window.addEventListener('resize', scheduleRelationUpdate)
  scheduleRelationUpdate()
})

onBeforeUnmount(() => {
  if (frame !== null) window.cancelAnimationFrame(frame)
  mutationObserver?.disconnect()
  resizeObserver?.disconnect()
  document.removeEventListener('scroll', scheduleRelationUpdate, true)
  window.removeEventListener('resize', scheduleRelationUpdate)
})
</script>

<template>
  <svg
    class="pointer-events-none fixed inset-0 z-30 h-screen w-screen overflow-visible"
    aria-hidden="true"
    data-testid="evidence-relation-overlay"
  >
    <path
      v-for="relation in relations"
      :key="relation.key"
      :d="relation.path"
      fill="none"
      :stroke="relation.color"
      :stroke-width="relation.strokeWidth"
      :stroke-opacity="relation.opacity"
      :stroke-dasharray="relation.stage === 'map-highlight' ? '3 3' : undefined"
      stroke-linecap="round"
      stroke-linejoin="round"
      vector-effect="non-scaling-stroke"
      :data-relation-stage="relation.stage"
      :data-relation-active="relation.active ? 'true' : 'false'"
    />
  </svg>
</template>
