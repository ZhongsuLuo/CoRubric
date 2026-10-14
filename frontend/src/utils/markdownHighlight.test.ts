import { describe, expect, it } from 'vitest'
import type { HighlightItem } from '../types/schema'
import { applyMarkdownHighlights } from './markdownHighlight'

const highlight = (id: string, text: string): HighlightItem => ({
  id,
  report_id: 'report-a',
  criteria_id: '1',
  score: 8,
  max_score: 10,
  reason: 'Grounded evidence',
  source: 'llm',
  color: 'green',
  anchor: { text, occurrence: 0 },
})

describe('markdown evidence rendering', () => {
  it('matches an image after the report API rewrites its relative source', () => {
    const container = document.createElement('div')
    container.innerHTML =
      '<img alt="instrument" src="/case-files/case%201/markdown/report-a/images/p3_crop0.png">'

    applyMarkdownHighlights(
      container,
      [highlight('image-evidence', 'images/p3_crop0.png')],
      null,
      '/api',
    )

    expect(container.querySelector('img')?.dataset.highlightId).toBe('image-evidence')
  })

  it('uses accessible KaTeX text when evidence spans an inline formula', () => {
    const container = document.createElement('div')
    container.innerHTML = `
      <p>光的吸收定律：
        <span class="katex">
          <span class="katex-mathml"><math><semantics><mrow><mi>A</mi><mo>=</mo><mi>ε</mi><mi>b</mi><mi>c</mi></mrow><annotation encoding="application/x-tex">A=εbc</annotation></semantics></math></span>
          <span class="katex-html">A=εbc</span>
        </span>
        ，吸光度与浓度成正比。
      </p>`

    applyMarkdownHighlights(
      container,
      [highlight('math-evidence', '光的吸收定律：$A=εbc$，吸光度与浓度成正比。')],
      null,
      '/api',
    )

    expect(container.querySelector('[data-highlight-id="math-evidence"]')).not.toBeNull()
  })

  it('matches a Markdown table row against rendered table cells', () => {
    const container = document.createElement('div')
    container.innerHTML = `
      <table><tbody><tr>
        <td>原理</td><td>基态原子吸收特定共振线</td><td>分子吸收紫外或可见光</td>
      </tr></tbody></table>`

    applyMarkdownHighlights(
      container,
      [highlight('table-evidence', '| 原理 | 基态原子吸收特定共振线 | 分子吸收紫外或可见光 |')],
      null,
      '/api',
    )

    expect(
      container.querySelectorAll(
        '[data-highlight-id="table-evidence"]:not([data-highlight-badge])',
      ),
    ).toHaveLength(3)
  })

  it('matches a long passage containing TeX subscripts without indexing duplicate KaTeX DOM', () => {
    const katex = (tex: string, visible: string) => `
      <span class="katex">
        <span class="katex-mathml"><math><semantics><mrow>${visible}</mrow><annotation encoding="application/x-tex">${tex}</annotation></semantics></math></span>
        <span class="katex-html">${visible}</span>
      </span>`
    const container = document.createElement('div')
    container.innerHTML = `
      <p>采用标准曲线法时，测出吸光度 ${katex('A_x', 'Ax')}，由 ${katex('A_x', 'Ax')} 查得浓度 ${katex('c_x', 'cx')}。</p>
      <p>采用标准加入法时，分别测定 ${katex('c_x', 'cx')}、${katex('c_x + c_s', 'cx+cs')} 的吸光度。</p>`

    applyMarkdownHighlights(
      container,
      [
        highlight(
          'subscript-evidence',
          '采用标准曲线法时，测出吸光度 $A_x$，由 $A_x$ 查得浓度 $c_x$。\n\n采用标准加入法时，分别测定 $c_x$、$c_x + c_s$ 的吸光度。',
        ),
      ],
      null,
      '/api',
    )

    expect(container.querySelector('[data-highlight-id="subscript-evidence"]')).not.toBeNull()
  })

  it('keeps overlapping evidence ranges valid while earlier highlights change the DOM', () => {
    const container = document.createElement('div')
    container.innerHTML = '<p>标准曲线法和标准加入法都用于定量分析。</p>'

    applyMarkdownHighlights(
      container,
      [
        highlight('long-evidence', '标准曲线法和标准加入法都用于定量分析。'),
        highlight('nested-evidence', '标准加入法'),
      ],
      null,
      '/api',
    )

    expect(container.querySelector('[data-highlight-id="long-evidence"]')).not.toBeNull()
    expect(container.querySelector('[data-highlight-id="nested-evidence"]')).not.toBeNull()
  })

  it('keeps a table-row criterion navigable when a broader answer cites the same cells', () => {
    const container = document.createElement('div')
    container.innerHTML = `
      <p>完整回答如下：</p>
      <table><tbody><tr>
        <td>原理</td><td>基态原子吸收特定共振线</td><td>分子吸收紫外或可见光</td>
      </tr></tbody></table>`

    applyMarkdownHighlights(
      container,
      [
        highlight(
          'broad-evidence',
          '完整回答如下：\n\n| 原理 | 基态原子吸收特定共振线 | 分子吸收紫外或可见光 |',
        ),
        highlight('row-evidence', '| 原理 | 基态原子吸收特定共振线 | 分子吸收紫外或可见光 |'),
      ],
      null,
      '/api',
    )

    expect(container.querySelector('[data-highlight-id="broad-evidence"]')).not.toBeNull()
    expect(container.querySelector('[data-highlight-id="row-evidence"]')).not.toBeNull()
  })

  it('keeps two criteria independently navigable when they cite identical evidence', () => {
    const container = document.createElement('div')
    container.innerHTML = `
      <ol><li><strong>标准加入法和标准曲线法的适用范围</strong>
        <ul>
          <li>标准曲线法用于简单基体。</li>
          <li>标准加入法用于复杂基体。</li>
        </ul>
      </li></ol>`
    const evidence =
      '1. **标准加入法和标准曲线法的适用范围**\n   - 标准曲线法用于简单基体。\n   - 标准加入法用于复杂基体。'
    const first = highlight('criterion-four', evidence)
    first.criteria_id = '4'
    const second = highlight('criterion-fourteen', evidence)
    second.criteria_id = '14'

    applyMarkdownHighlights(container, [first, second], null, '/api')

    expect(container.querySelector('[data-highlight-id="criterion-four"]')).not.toBeNull()
    expect(container.querySelector('[data-highlight-id="criterion-fourteen"]')).not.toBeNull()
  })
})
