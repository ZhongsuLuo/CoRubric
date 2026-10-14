import { describe, expect, it } from 'vitest'
import {
  parseLegacyImageEvidence,
  resolveReportImageUrl,
  safeReportImageSrc,
} from './imageEvidence'

describe('image evidence utilities', () => {
  it('parses legacy image descriptions into structured anchors', () => {
    expect(
      parseLegacyImageEvidence(
        '> **[Image]** Calibration increases linearly.\n> *Source: images/chart.png*',
      ),
    ).toEqual([
      {
        anchor_type: 'image',
        image_src: 'images/chart.png',
        description: 'Calibration increases linearly.',
      },
    ])
  })

  it.each([
    'https://example.com/chart.png',
    '//example.com/case-files/chart.png',
    'images/../secret.png',
    'images/%2e%2e/secret.png',
    'images/%252e%252e/secret.png',
  ])('rejects unsafe image source %s', (source) => {
    expect(safeReportImageSrc(source)).toBe('')
  })

  it('resolves a catalog identity to the report-owned image URL', () => {
    const markdown = '![Chart](/case-files/case/markdown/report/images/chart.png)'
    expect(resolveReportImageUrl(markdown, 'images/chart.png')).toBe(
      '/api/case-files/case/markdown/report/images/chart.png',
    )
  })
})
