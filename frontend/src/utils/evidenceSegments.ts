import type { HighlightAnchor } from '../types/schema'

type SourceBlock = { start: number; end: number }
type LocatedTextAnchor = {
  anchor: HighlightAnchor
  start: number
  end: number
  blockStart: number
  blockEnd: number
}

const sourceBlocks = (markdown: string): SourceBlock[] => {
  const blocks: SourceBlock[] = []
  const separator = /\n[ \t]*\n/g
  let start = 0

  const addBlock = (rawStart: number, rawEnd: number) => {
    let blockStart = rawStart
    let blockEnd = rawEnd
    while (blockStart < blockEnd && /\s/.test(markdown[blockStart] || '')) blockStart += 1
    while (blockEnd > blockStart && /\s/.test(markdown[blockEnd - 1] || '')) blockEnd -= 1
    if (blockStart < blockEnd) blocks.push({ start: blockStart, end: blockEnd })
  }

  for (const match of markdown.matchAll(separator)) {
    const index = match.index ?? start
    addBlock(start, index)
    start = index + match[0].length
  }
  addBlock(start, markdown.length)
  return blocks
}

const comparableText = (value: string) =>
  value
    .normalize('NFKC')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*_~`$|]/g, '')
    .replace(/\s+/g, '')
    .toLowerCase()

const nthExactRange = (source: string, search: string, occurrence: number) => {
  let found = -1
  let from = 0
  for (let index = 0; index <= occurrence; index += 1) {
    found = source.indexOf(search, from)
    if (found < 0) return null
    from = found + Math.max(search.length, 1)
  }
  return { start: found, end: found + search.length }
}

const enclosingBlockRange = (blocks: SourceBlock[], start: number, end: number): SourceBlock => {
  const first = blocks.find((block) => start >= block.start && start <= block.end)
  const last = [...blocks].reverse().find((block) => end >= block.start && end <= block.end)
  return {
    start: first?.start ?? start,
    end: last?.end ?? end,
  }
}

const locateTextAnchor = (
  markdown: string,
  blocks: SourceBlock[],
  anchor: HighlightAnchor,
): LocatedTextAnchor | null => {
  if ((anchor.anchor_type || 'text') !== 'text') return null
  const text = anchor.text.trim()
  if (!text) return null

  const offsetStart = Number(anchor.start_offset)
  const offsetEnd = Number(anchor.end_offset)
  let range =
    Number.isInteger(offsetStart) &&
    Number.isInteger(offsetEnd) &&
    offsetStart >= 0 &&
    offsetEnd > offsetStart &&
    offsetEnd <= markdown.length
      ? { start: offsetStart, end: offsetEnd }
      : null

  if (!range) {
    const occurrence = Number.isFinite(anchor.occurrence) ? Number(anchor.occurrence) : 0
    range = nthExactRange(markdown, text, Math.max(occurrence, 0))
  }

  if (!range) {
    const needle = comparableText(text)
    if (!needle) return null
    const occurrence = Number.isFinite(anchor.occurrence) ? Number(anchor.occurrence) : 0
    const matches = blocks.filter((block) => {
      const candidate = comparableText(markdown.slice(block.start, block.end))
      return candidate === needle || candidate.includes(needle)
    })
    const block = matches[Math.max(occurrence, 0)]
    if (!block) return null
    range = { start: block.start, end: block.end }
  }

  const blockRange = enclosingBlockRange(blocks, range.start, range.end)
  return {
    anchor,
    start: range.start,
    end: range.end,
    blockStart: blockRange.start,
    blockEnd: blockRange.end,
  }
}

const canMergeTextAnchors = (
  markdown: string,
  previous: LocatedTextAnchor,
  current: LocatedTextAnchor,
) => {
  if ((previous.anchor.anchor_type || 'text') !== 'text') return false
  if ((current.anchor.anchor_type || 'text') !== 'text') return false
  if (current.start < previous.start) return false

  if (current.start <= previous.end) return true
  if (previous.blockStart === current.blockStart && previous.blockEnd === current.blockEnd) {
    return /^\s*$/.test(markdown.slice(previous.end, current.start))
  }
  return /^\s*$/.test(markdown.slice(previous.blockEnd, current.blockStart))
}

/**
 * Treat adjacent text blocks cited by one grade as one logical evidence segment.
 * Non-adjacent anchors and mixed media remain separate so the UI can navigate them.
 */
export const mergeContiguousTextEvidenceAnchors = (
  markdown: string,
  anchors: HighlightAnchor[],
): HighlightAnchor[] => {
  if (!markdown || anchors.length < 2) return anchors
  const blocks = sourceBlocks(markdown)
  const merged: Array<{ anchor: HighlightAnchor; location: LocatedTextAnchor | null }> = []

  anchors.forEach((anchor) => {
    const location = locateTextAnchor(markdown, blocks, anchor)
    const previous = merged[merged.length - 1]
    if (
      !location ||
      !previous?.location ||
      !canMergeTextAnchors(markdown, previous.location, location)
    ) {
      merged.push({ anchor, location })
      return
    }

    const start = Math.min(previous.location.blockStart, location.blockStart)
    const end = Math.max(previous.location.blockEnd, location.blockEnd)
    const combined: HighlightAnchor = {
      ...previous.anchor,
      anchor_type: 'text',
      text: markdown.slice(start, end),
      occurrence: 0,
      start_offset: start,
      end_offset: end,
    }
    previous.anchor = combined
    previous.location = {
      anchor: combined,
      start,
      end,
      blockStart: start,
      blockEnd: end,
    }
  })

  return merged.map((item) => item.anchor)
}
