import { describe, expect, it } from 'vitest'
import type { HighlightAnchor } from '../types/schema'
import { mergeContiguousTextEvidenceAnchors } from './evidenceSegments'

describe('logical evidence segments', () => {
  it('merges adjacent text paragraphs into one source-backed segment', () => {
    const markdown = '# 浙江大学 实验报告\n\n**姓名**: 穆高山\n\n**课程名称**: 大学化学实验'
    const anchors: HighlightAnchor[] = [
      { anchor_type: 'text', text: '浙江大学 实验报告' },
      { anchor_type: 'text', text: '姓名: 穆高山' },
    ]

    expect(mergeContiguousTextEvidenceAnchors(markdown, anchors)).toEqual([
      expect.objectContaining({
        anchor_type: 'text',
        text: '# 浙江大学 实验报告\n\n**姓名**: 穆高山',
        occurrence: 0,
      }),
    ])
  })

  it('keeps text anchors separate when an uncited paragraph sits between them', () => {
    const markdown = 'First evidence.\n\nUnrelated paragraph.\n\nSecond evidence.'
    const anchors: HighlightAnchor[] = [
      { anchor_type: 'text', text: 'First evidence.' },
      { anchor_type: 'text', text: 'Second evidence.' },
    ]

    expect(mergeContiguousTextEvidenceAnchors(markdown, anchors)).toEqual(anchors)
  })

  it('keeps adjacent text and image evidence as separate segments', () => {
    const markdown = 'Text evidence.\n\n![Chart](images/chart.png)'
    const anchors: HighlightAnchor[] = [
      { anchor_type: 'text', text: 'Text evidence.' },
      { anchor_type: 'image', text: 'images/chart.png', image_src: 'images/chart.png' },
    ]

    expect(mergeContiguousTextEvidenceAnchors(markdown, anchors)).toEqual(anchors)
  })
})
