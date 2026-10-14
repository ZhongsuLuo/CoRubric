import type { HighlightItem } from '../types/schema'

type TextMapEntry = { start: number; end: number }
type MappedText = { normalized: string; normalizedToOriginal: TextMapEntry[] }
type TextSegmentWithOffset = {
  start: number
  end: number
  text: string
  block: Element
  startNode: Node
  startOffset: number
  endNode: Node
  endOffset: number
}
type TextRange = { start: number; end: number }
type HighlightRangePart = {
  startNode: Node
  startOffset: number
  endNode: Node
  endOffset: number
  block: Element
  text: string
  globalStart: number
  globalEnd: number
}
type HighlightTextIndex = {
  fullText: string
  segments: TextSegmentWithOffset[]
  compact: MappedText
  normalized: MappedText
}

const blockSelector = 'p, li, h1, h2, h3, h4, h5, h6, td, th, blockquote'

const highlightColorClass: Record<HighlightItem['color'], string> = {
  amber: 'evidence-highlight-review',
  blue: 'evidence-highlight-teacher',
  green: 'evidence-highlight-model',
  purple: 'evidence-highlight-pending',
  red: 'evidence-highlight-conflict',
}

const imageRingClass: Record<HighlightItem['color'], string> = {
  amber: 'evidence-ring-review',
  blue: 'evidence-ring-teacher',
  green: 'evidence-ring-model',
  purple: 'evidence-ring-pending',
  red: 'evidence-ring-conflict',
}

const scoreBadgeColorClass: Record<HighlightItem['color'], string> = {
  amber: 'evidence-badge-review',
  blue: 'evidence-badge-teacher',
  green: 'evidence-badge-model',
  purple: 'evidence-badge-pending',
  red: 'evidence-badge-conflict',
}

const scoreBadgeBaseClass =
  'absolute -top-4 left-0 z-10 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-bold leading-none shadow-sm ring-1 pointer-events-none opacity-0 transition-opacity'
const highlightSpanSelector = 'span[data-highlight-id]'
const renderedHighlightSpanSelector = `${highlightSpanSelector}:not([data-highlight-badge])`

const activeHighlightClass: Record<HighlightItem['color'], string> = {
  amber: 'evidence-active-review',
  blue: 'evidence-active-teacher',
  green: 'evidence-active-model',
  purple: 'evidence-active-pending',
  red: 'evidence-active-conflict',
}

const nestedActiveHighlightClass: Record<HighlightItem['color'], string> = {
  amber: 'evidence-nested-review',
  blue: 'evidence-nested-teacher',
  green: 'evidence-nested-model',
  purple: 'evidence-nested-pending',
  red: 'evidence-nested-conflict',
}

export const normalizeSelectedText = (value: string) =>
  value
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]*[\r\n]+[ \t]*/g, ' ')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()

const normalizeMatchChar = (char: string) =>
  char
    .normalize('NFKC')
    .replace(/[\u200B-\u200D\u2060\uFEFF]/g, '')
    .replace(/[′＇‘’`]/g, "'")
    .toLowerCase()

const normalizedTextWithMap = (value: string): MappedText => {
  let normalized = ''
  const normalizedToOriginal: TextMapEntry[] = []
  let offset = 0

  for (const char of value) {
    const normalizedChar = normalizeMatchChar(char)
    Array.from(normalizedChar).forEach((part) => {
      if (!part || /\s/.test(part)) return
      normalized += part
      normalizedToOriginal.push({ start: offset, end: offset + char.length })
    })
    offset += char.length
  }

  return { normalized, normalizedToOriginal }
}

const compactTextWithMap = (value: string): MappedText => {
  let normalized = ''
  const normalizedToOriginal: TextMapEntry[] = []
  let offset = 0

  for (const char of value) {
    if (/\s/.test(char)) {
      offset += char.length
      continue
    }
    normalized += char
    normalizedToOriginal.push({ start: offset, end: offset + char.length })
    offset += char.length
  }

  return { normalized, normalizedToOriginal }
}

const rangeFromMappedIndex = (
  index: number,
  length: number,
  map: TextMapEntry[],
): TextRange | null => {
  const originalStart = map[index]?.start
  const originalEnd = map[index + length - 1]?.end
  if (originalStart === undefined || originalEnd === undefined) return null
  return { start: originalStart, end: originalEnd }
}

const findExactRange = (fullText: string, search: string, occurrence: number): TextRange | null => {
  let count = 0
  let searchFrom = 0

  while (searchFrom <= fullText.length) {
    const exactIndex = fullText.indexOf(search, searchFrom)
    if (exactIndex < 0) break
    if (count === occurrence) return { start: exactIndex, end: exactIndex + search.length }

    count += 1
    searchFrom = exactIndex + Math.max(search.length, 1)
  }

  return null
}

const findMappedRange = (
  haystack: MappedText,
  search: string,
  mapper: (value: string) => MappedText,
): TextRange | null => {
  const needle = mapper(search).normalized
  if (!needle) return null

  const index = haystack.normalized.indexOf(needle)
  return index < 0
    ? null
    : rangeFromMappedIndex(index, needle.length, haystack.normalizedToOriginal)
}

const stripMarkdownAnchorSyntax = (value: string) =>
  value
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*_~`]+/g, '')

const stripMarkdownBlockSyntax = (value: string) =>
  value
    .replace(/^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$/gm, '')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*~`$]+/g, '')
    .replace(/^\s*\|/gm, '')
    .replace(/\|\s*$/gm, '')
    .replace(/\s*\|\s*/g, '')

const searchVariants = (search: string) =>
  Array.from(
    new Set(
      [search, stripMarkdownBlockSyntax(search), stripMarkdownAnchorSyntax(search)]
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  )

const findHighlightRange = (
  index: HighlightTextIndex,
  search: string,
  occurrence: number,
): TextRange | null => {
  for (const variant of searchVariants(search)) {
    const match =
      findExactRange(index.fullText, variant, occurrence) ||
      findMappedRange(index.compact, variant, compactTextWithMap) ||
      findMappedRange(index.normalized, variant, normalizedTextWithMap)
    if (match) return match
  }
  return null
}

const buildHighlightTextIndex = (container: HTMLElement): HighlightTextIndex => {
  const segments: TextSegmentWithOffset[] = []
  let offset = 0

  const addSegment = (
    text: string,
    block: Element,
    startNode: Node,
    startOffset: number,
    endNode: Node,
    endOffset: number,
  ) => {
    if (!text) return
    const start = offset
    offset += text.length
    segments.push({
      start,
      end: offset,
      text,
      block,
      startNode,
      startOffset,
      endNode,
      endOffset,
    })
  }

  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const textNode = node as Text
      const parent = textNode.parentElement
      const text = textNode.nodeValue || ''
      const block = parent?.closest(blockSelector) || parent
      if (parent && block && !parent.closest('script, style')) {
        addSegment(text, block, textNode, 0, textNode, text.length)
      }
      return
    }
    if (!(node instanceof Element) || node.matches('script, style')) return

    if (node.matches('.katex')) {
      const annotation = node.querySelector('annotation[encoding="application/x-tex"]')
      const formula = annotation?.textContent?.trim() || ''
      const parent = node.parentNode
      const block = node.closest(blockSelector) || node.parentElement
      if (formula && parent && block) {
        const childIndex = Array.prototype.indexOf.call(parent.childNodes, node) as number
        addSegment(formula, block, parent, childIndex, parent, childIndex + 1)
      }
      return
    }

    Array.from(node.childNodes).forEach(visit)
  }

  Array.from(container.childNodes).forEach(visit)
  const fullText = segments.map((item) => item.text).join('')
  return {
    fullText,
    segments,
    compact: compactTextWithMap(fullText),
    normalized: normalizedTextWithMap(fullText),
  }
}

const highlightClass = (highlight: HighlightItem, activeIds: ReadonlySet<string>) => {
  const activeClass = activeIds.has(highlight.id)
    ? ` ${activeHighlightClass[highlight.color]}`
    : ' hover:bg-opacity-80 transition-colors'

  return `${highlightColorClass[highlight.color]}${activeClass} relative cursor-pointer px-0.5 rounded-sm align-baseline leading-[inherit]`
}

export const syncMarkdownHighlightActiveState = (
  container: HTMLElement,
  highlights: HighlightItem[],
  activeHighlightId: string | null,
  activeHighlightIds: readonly string[] = [],
) => {
  const byId = new Map(highlights.map((highlight) => [highlight.id, highlight]))
  const activeIds = new Set([
    ...(activeHighlightId ? [activeHighlightId] : []),
    ...activeHighlightIds,
  ])
  container.querySelectorAll<HTMLElement>(renderedHighlightSpanSelector).forEach((element) => {
    const highlightId = element.dataset.highlightId
    const highlight = highlightId ? byId.get(highlightId) : null
    if (highlight) {
      element.className = highlightClass(highlight, activeIds)
    }
  })
  activeIds.forEach((activeId) => {
    const activeHighlight = byId.get(activeId)
    const activeElements = Array.from(
      container.querySelectorAll<HTMLElement>(renderedHighlightSpanSelector),
    ).filter((element) => element.dataset.highlightId === activeId)
    if (activeHighlight) {
      activeElements.forEach((activeElement) => {
        activeElement
          .querySelectorAll<HTMLElement>(renderedHighlightSpanSelector)
          .forEach((nestedElement) => {
            nestedElement.className = `${nestedActiveHighlightClass[activeHighlight.color]} relative cursor-pointer px-0.5 align-baseline leading-[inherit]`
          })
      })
    }
  })
  container.querySelectorAll<HTMLImageElement>('img[data-highlight-id]').forEach((image) => {
    const active = activeIds.has(image.dataset.highlightId || '')
    image.classList.toggle('ring-4', active)
    image.classList.toggle('ring-offset-2', active)
    image.classList.toggle('ring-2', !active)
    image.classList.toggle('ring-offset-1', !active)
  })
  container.querySelectorAll<HTMLElement>('[data-highlight-badge]').forEach((badge) => {
    badge.style.opacity = badge.dataset.highlightId === activeHighlightId ? '1' : ''
  })
}

const createHighlightSpan = (
  container: HTMLElement,
  highlight: HighlightItem,
  activeIds: ReadonlySet<string>,
) => {
  const span = container.ownerDocument.createElement('span')
  span.dataset.highlightId = highlight.id
  span.dataset.criteriaId = highlight.criteria_id
  span.className = highlightClass(highlight, activeIds)
  return span
}

const createLiveRange = (
  container: HTMLElement,
  startNode: Node,
  startOffset: number,
  endNode: Node,
  endOffset: number,
) => {
  const range = container.ownerDocument.createRange()
  range.setStart(startNode, startOffset)
  range.setEnd(endNode, endOffset)
  return range
}

const wrapTextRange = (
  container: HTMLElement,
  range: Range,
  highlight: HighlightItem,
  activeIds: ReadonlySet<string>,
) => {
  if (range.collapsed) return null

  try {
    const span = createHighlightSpan(container, highlight, activeIds)
    const contents = range.extractContents()
    span.appendChild(contents)
    range.insertNode(span)
    return span
  } catch (err) {
    console.warn('[markdown] wrapOccurrence failed', err)
    return null
  }
}

const wrapExistingHighlight = (
  container: HTMLElement,
  existing: HTMLElement,
  highlight: HighlightItem,
  activeIds: ReadonlySet<string>,
) => {
  const parent = existing.parentNode
  if (!parent) return null
  const span = createHighlightSpan(container, highlight, activeIds)
  parent.insertBefore(span, existing)
  span.appendChild(existing)
  return span
}

const partsForRange = (
  segments: TextSegmentWithOffset[],
  match: TextRange,
): HighlightRangePart[] => {
  const parts: HighlightRangePart[] = []

  for (const item of segments) {
    const overlapStart = Math.max(match.start, item.start)
    const overlapEnd = Math.min(match.end, item.end)
    if (overlapStart >= overlapEnd) continue

    const isTextSegment =
      item.startNode === item.endNode && item.startNode.nodeType === Node.TEXT_NODE
    const startOffset = isTextSegment
      ? item.startOffset + overlapStart - item.start
      : item.startOffset
    const endOffset = isTextSegment ? item.startOffset + overlapEnd - item.start : item.endOffset
    const segment = item.text.slice(overlapStart - item.start, overlapEnd - item.start)
    if (!segment.trim()) continue

    parts.push({
      startNode: item.startNode,
      startOffset,
      endNode: item.endNode,
      endOffset,
      block: item.block,
      text: segment,
      globalStart: overlapStart,
      globalEnd: overlapEnd,
    })
  }

  return parts
}

const groupPartsByBlock = (parts: HighlightRangePart[]) => {
  const groups: HighlightRangePart[][] = []

  parts.forEach((part) => {
    const group = groups[groups.length - 1]
    if (group && group[0]?.block === part.block) {
      group.push(part)
    } else {
      groups.push([part])
    }
  })

  return groups
}

const displayScore = (value: number) => String(Number(value.toFixed(2)))

const addScoreBadge = (container: HTMLElement, highlight: HighlightItem) => {
  const maxScore = Number(highlight.max_score)
  if (!Number.isFinite(maxScore)) return
  const firstSegment = Array.from(
    container.querySelectorAll<HTMLSpanElement>(renderedHighlightSpanSelector),
  ).find((element) => element.dataset.highlightId === highlight.id)
  if (!firstSegment) return

  const badge = container.ownerDocument.createElement('span')
  badge.dataset.highlightId = highlight.id
  badge.dataset.highlightBadge = 'true'
  badge.setAttribute('aria-hidden', 'true')
  badge.className = `${scoreBadgeBaseClass} ${scoreBadgeColorClass[highlight.color]}`
  badge.textContent = `${displayScore(highlight.score)}/${displayScore(maxScore)}`
  firstSegment.prepend(badge)
}

const matchTextHighlight = (index: HighlightTextIndex, highlight: HighlightItem) => {
  const search = highlight.anchor.text.trim()
  if (!search) return []
  const occurrence = Number.isFinite(highlight.anchor.occurrence)
    ? Number(highlight.anchor.occurrence)
    : 0
  const match = findHighlightRange(index, search, occurrence)
  return match ? groupPartsByBlock(partsForRange(index.segments, match)) : []
}

const applyImageHighlight = (container: HTMLElement, highlight: HighlightItem) => {
  const needle = highlight.anchor?.text?.trim()
  if (!needle) return

  const normalizeImageIdentity = (value: string) => {
    const withoutQuery = value.split(/[?#]/, 1)[0] || ''
    try {
      return decodeURIComponent(withoutQuery).replace(/\\/g, '/').replace(/^\.\//, '')
    } catch {
      return withoutQuery.replace(/\\/g, '/').replace(/^\.\//, '')
    }
  }
  const normalizedNeedle = normalizeImageIdentity(needle)

  const imgs = Array.from(container.querySelectorAll('img')) as HTMLImageElement[]
  imgs.forEach((img) => {
    const alt = (img.getAttribute('alt') || '').trim()
    const title = (img.getAttribute('title') || '').trim()
    const src = (img.getAttribute('src') || '').trim()
    const normalizedSrc = normalizeImageIdentity(src)
    const sourceMatches =
      normalizedSrc === normalizedNeedle ||
      normalizedSrc.endsWith(`/${normalizedNeedle}`) ||
      normalizedNeedle.endsWith(`/${normalizedSrc}`)
    if (![alt, title].includes(needle) && !sourceMatches) return

    img.dataset.highlightId = highlight.id
    img.dataset.criteriaId = highlight.criteria_id
    img.classList.add('ring-2', 'ring-offset-1', imageRingClass[highlight.color], 'rounded')
  })
}

const normalizeImageSources = (container: HTMLElement, apiBase: string) => {
  container.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src') || ''
    if (src.startsWith('/case-files/')) {
      img.setAttribute('src', `${apiBase}${src}`)
    }
  })
}

export const applyMarkdownHighlights = (
  container: HTMLElement,
  highlights: HighlightItem[],
  activeHighlightId: string | null,
  apiBase: string,
) => {
  const activeIds = new Set(activeHighlightId ? [activeHighlightId] : [])
  normalizeImageSources(container, apiBase)
  const index = buildHighlightTextIndex(container)
  const textMatches = highlights.flatMap((highlight) =>
    matchTextHighlight(index, highlight).map((group) => ({ highlight, group })),
  )
  const liveTextMatches = textMatches.map(({ highlight, group }) => {
    const first = group[0]
    const last = group[group.length - 1]
    return {
      highlight,
      group,
      range:
        first && last
          ? createLiveRange(
              container,
              first.startNode,
              first.startOffset,
              last.endNode,
              last.endOffset,
            )
          : null,
    }
  })

  highlights.forEach((highlight) => {
    applyImageHighlight(container, highlight)
  })

  const renderedRanges = new Map<string, HTMLElement>()
  liveTextMatches
    .sort((left, right) => {
      const leftStart = left.group[0]?.globalStart ?? 0
      const rightStart = right.group[0]?.globalStart ?? 0
      if (leftStart !== rightStart) return rightStart - leftStart
      const leftEnd = left.group[left.group.length - 1]?.globalEnd ?? leftStart
      const rightEnd = right.group[right.group.length - 1]?.globalEnd ?? rightStart
      if (leftEnd !== rightEnd) return leftEnd - rightEnd
      // When a specific criterion and a broader criterion cite the same rendered
      // segment (for example one table row inside a whole answer), wrap the
      // shorter anchor first so both identities remain navigable.
      return left.highlight.anchor.text.length - right.highlight.anchor.text.length
    })
    .forEach(({ highlight, group, range }) => {
      const text = group.map((part) => part.text).join('')
      if (!text.trim() || !range) return
      const first = group[0]
      const last = group[group.length - 1]
      const rangeKey = `${first?.globalStart ?? 0}:${last?.globalEnd ?? 0}`
      const existing = renderedRanges.get(rangeKey)
      const rendered = existing
        ? wrapExistingHighlight(container, existing, highlight, activeIds)
        : wrapTextRange(container, range, highlight, activeIds)
      if (rendered) renderedRanges.set(rangeKey, rendered)
    })

  highlights.forEach((highlight) => addScoreBadge(container, highlight))
}
