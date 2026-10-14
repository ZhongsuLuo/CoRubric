import { describe, expect, it } from 'vitest'
import type { HighlightItem } from '../types/schema'
import { buildGlobalEvidenceBlocks } from './globalEvidence'

const highlight = (text: string, occurrence = 0): HighlightItem => ({
  id: 'test-highlight',
  report_id: 'report-a',
  criteria_id: '1',
  score: 8,
  max_score: 10,
  reason: '',
  source: 'teacher',
  color: 'blue',
  anchor: { text, occurrence },
})

const textContent = (html: string) => {
  const container = document.createElement('div')
  container.innerHTML = html
  return container.textContent || ''
}

describe('buildGlobalEvidenceBlocks', () => {
  it('returns the complete paragraph and keeps only the selected text highlighted', () => {
    const result = buildGlobalEvidenceBlocks(
      '# Report\n\nThe complete paragraph contains selected evidence and useful context.\n\nAnother paragraph.',
      highlight('selected evidence'),
      '/api',
    )

    expect(result.found).toBe(true)
    expect(result.blocks).toHaveLength(1)
    expect(result.blocks[0]!.html).toContain('The complete paragraph contains')
    expect(result.blocks[0]!.html).toContain('data-highlight-id="test-highlight"')
    expect(result.blocks[0]!.html).not.toContain('data-highlight-badge')
    expect(result.blocks[0]!.html).not.toContain('Another paragraph')
    expect(result.blocks[0]!.html).not.toContain('evidence-active-teacher')
  })

  it('adds the selection outline only when the evidence is active', () => {
    const item = highlight('selected evidence')
    const inactive = buildGlobalEvidenceBlocks('Paragraph with selected evidence.', item, '/api')
    const active = buildGlobalEvidenceBlocks(
      'Paragraph with selected evidence.',
      item,
      '/api',
      item.id,
    )

    expect(inactive.blocks[0]!.html).not.toContain('evidence-active-teacher')
    expect(active.blocks[0]!.html).toContain('evidence-active-teacher')
  })

  it('uses the original occurrence rule for repeated evidence', () => {
    const result = buildGlobalEvidenceBlocks(
      'First repeated evidence paragraph.\n\nSecond repeated evidence paragraph.',
      highlight('repeated evidence', 1),
      '/api',
    )

    expect(result.blocks).toHaveLength(1)
    expect(textContent(result.blocks[0]!.html)).toContain('Second repeated evidence paragraph')
    expect(textContent(result.blocks[0]!.html)).not.toContain('First repeated evidence paragraph')
  })

  it('returns a whole table when evidence is in one cell', () => {
    const result = buildGlobalEvidenceBlocks(
      '| Metric | Value |\n| --- | ---: |\n| Average | 10.0 |',
      highlight('Average'),
      '/api',
    )

    expect(result.found).toBe(true)
    expect(result.blocks[0]!.html).toMatch(/^<table>/)
    expect(result.blocks[0]!.html).toContain('<th>Metric</th>')
    expect(textContent(result.blocks[0]!.html)).toContain('Average')
    expect(textContent(result.blocks[0]!.html)).toContain('10.0')
  })

  it('returns the complete image block when image alt text is selected', () => {
    const result = buildGlobalEvidenceBlocks(
      'Before.\n\n![Calibration image](data:image/png;base64,AAAA "calibration")\n\nAfter.',
      highlight('Calibration image'),
      '/api',
    )

    expect(result.found).toBe(true)
    expect(result.blocks).toHaveLength(1)
    expect(result.blocks[0]!.html).toContain('<img')
    expect(result.blocks[0]!.html).toContain('data-highlight-id="test-highlight"')
    expect(result.blocks[0]!.html).not.toContain('Before.')
  })

  it('keeps complete list and quote semantic blocks', () => {
    const list = buildGlobalEvidenceBlocks(
      '- Item with selected list evidence\n- Other item',
      highlight('selected list evidence'),
      '/api',
    )
    const quote = buildGlobalEvidenceBlocks(
      '> Quoted selected evidence with context.',
      highlight('selected evidence'),
      '/api',
    )

    expect(list.blocks[0]!.html).toMatch(/^<li>/)
    expect(list.blocks[0]!.html).toContain('Item with')
    expect(quote.blocks[0]!.html).toMatch(/^<blockquote>/)
    expect(quote.blocks[0]!.html).toContain('Quoted')
  })

  it('returns multiple ordered blocks when one selection spans paragraphs', () => {
    const result = buildGlobalEvidenceBlocks(
      'First block ends with alpha.\n\nBeta begins the second block.',
      highlight('alpha. Beta'),
      '/api',
    )

    expect(result.found).toBe(true)
    expect(result.blocks).toHaveLength(2)
    expect(result.blocks[0]!.html).toContain('First block')
    expect(result.blocks[1]!.html).toContain('second block')
    expect(result.blocks[1]!.separatedBefore).toBe(false)
  })

  it('adds a separator only when undisplayed content exists between evidence blocks', () => {
    const first = highlight('First selected paragraph')
    const third = {
      ...highlight('Third selected paragraph'),
      id: 'third-highlight',
    }
    const result = buildGlobalEvidenceBlocks(
      'First selected paragraph.\n\nThis paragraph is not selected.\n\nThird selected paragraph.',
      [first, third],
      '/api',
    )

    expect(result.blocks).toHaveLength(2)
    expect(result.blocks[0]!.separatedBefore).toBe(false)
    expect(result.blocks[1]!.separatedBefore).toBe(true)
  })

  it('returns a structured missing result instead of dropping the score', () => {
    expect(
      buildGlobalEvidenceBlocks('Visible report text.', highlight('not present'), '/api'),
    ).toEqual({
      found: false,
      blocks: [],
    })
    expect(buildGlobalEvidenceBlocks('Visible report text.', highlight(''), '/api')).toEqual({
      found: false,
      blocks: [],
    })
  })
})
