import type { HighlightItem } from '../types/schema'
import { applyMarkdownHighlights } from './markdownHighlight'
import { createMarkdownRenderer } from './markdownMath'

export interface GlobalEvidenceBlocks {
  found: boolean
  blocks: Array<{
    html: string
    separatedBefore: boolean
  }>
}

const semanticBlockFor = (target: Element): Element | null => {
  // Order matters: evidence inside a cell represents the whole table, and
  // evidence inside a paragraph nested in a list/quote represents that whole
  // semantic item rather than only the inner paragraph.
  return (
    target.closest('table') ||
    target.closest('blockquote') ||
    target.closest('li') ||
    target.closest('pre') ||
    target.closest('figure') ||
    (target.tagName === 'IMG' ? target.closest('p, div') : null) ||
    target.closest('p, h1, h2, h3, h4, h5, h6') ||
    target
  )
}

const uniqueSemanticBlocks = (targets: Element[]) => {
  const blocks: Element[] = []
  targets.forEach((target) => {
    const block = semanticBlockFor(target)
    if (!block || blocks.includes(block)) return
    // If an earlier block already contains this one, the content is already
    // represented. If this block contains an earlier one, prefer the larger
    // semantic unit.
    const ancestorIndex = blocks.findIndex((item) => block.contains(item))
    if (ancestorIndex >= 0) blocks.splice(ancestorIndex, 1, block)
    else if (!blocks.some((item) => item.contains(block))) blocks.push(block)
  })
  return blocks
}

const hasMeaningfulContentBetween = (before: Element, after: Element) => {
  if (!(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING)) return false
  const range = before.ownerDocument.createRange()
  range.setStartAfter(before)
  range.setEndBefore(after)
  const skipped = range.cloneContents()
  if ((skipped.textContent || '').replace(/\s+/g, '').length > 0) return true
  return Boolean(
    skipped.querySelector(
      'img, table, figure, pre, blockquote, hr, video, audio, iframe, canvas, svg',
    ),
  )
}

export const buildGlobalEvidenceBlocks = (
  markdown: string,
  highlight: HighlightItem | HighlightItem[],
  apiBase: string,
  activeHighlightId: string | null = null,
): GlobalEvidenceBlocks => {
  const highlights = (Array.isArray(highlight) ? highlight : [highlight]).filter((item) =>
    item.anchor.text.trim(),
  )
  if (!markdown || !highlights.length || typeof document === 'undefined') {
    return { found: false, blocks: [] }
  }

  const container = document.createElement('div')
  container.innerHTML = createMarkdownRenderer().render(markdown)
  applyMarkdownHighlights(container, highlights, activeHighlightId, apiBase)

  const highlightIds = new Set(highlights.map((item) => item.id))
  const targets = Array.from(
    container.querySelectorAll('[data-highlight-id]:not([data-highlight-badge])'),
  ).filter((target) => highlightIds.has(target.getAttribute('data-highlight-id') || ''))
  const blocks = uniqueSemanticBlocks(targets)

  return {
    found: blocks.length > 0,
    blocks: blocks.map((block, index) => {
      const clone = block.cloneNode(true) as Element
      clone.querySelectorAll('[data-highlight-badge]').forEach((badge) => badge.remove())
      return {
        html: clone.outerHTML,
        separatedBefore: index > 0 && hasMeaningfulContentBetween(blocks[index - 1]!, block),
      }
    }),
  }
}
