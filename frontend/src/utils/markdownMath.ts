import MarkdownIt from 'markdown-it'
import katex from 'katex'

const renderMath = (source: string, displayMode: boolean) =>
  katex.renderToString(source, {
    displayMode,
    throwOnError: false,
    strict: 'ignore',
  })

const findClosingDollar = (source: string, start: number) => {
  for (let index = start; index < source.length; index += 1) {
    if (source[index] === '$' && source[index - 1] !== '\\') {
      return index
    }
  }
  return -1
}

const mathPlugin = (parser: MarkdownIt) => {
  parser.inline.ruler.after('escape', 'math_inline', (state, silent) => {
    if (state.src[state.pos] !== '$' || state.src[state.pos + 1] === '$') return false
    const end = findClosingDollar(state.src, state.pos + 1)
    if (end < 0) return false

    const content = state.src.slice(state.pos + 1, end)
    if (!content.trim()) return false

    if (!silent) {
      const token = state.push('math_inline', 'math', 0)
      token.content = content
      token.markup = '$'
    }
    state.pos = end + 1
    return true
  })

  parser.block.ruler.after('blockquote', 'math_block', (state, startLine, endLine, silent) => {
    const start = (state.bMarks[startLine] ?? 0) + (state.tShift[startLine] ?? 0)
    const max = state.eMarks[startLine] ?? start
    const firstLine = state.src.slice(start, max)
    const openIndex = firstLine.indexOf('$$')
    if (openIndex < 0 || firstLine.slice(0, openIndex).trim()) return false
    if (silent) return true

    const firstContent = firstLine.slice(openIndex + 2)
    const sameLineClose = firstContent.indexOf('$$')
    const contentLines: string[] = []
    let nextLine = startLine + 1

    if (sameLineClose >= 0) {
      contentLines.push(firstContent.slice(0, sameLineClose))
    } else {
      contentLines.push(firstContent)
      for (; nextLine < endLine; nextLine += 1) {
        const lineStart = (state.bMarks[nextLine] ?? 0) + (state.tShift[nextLine] ?? 0)
        const lineMax = state.eMarks[nextLine] ?? lineStart
        const line = state.src.slice(lineStart, lineMax)
        const closeIndex = line.indexOf('$$')
        if (closeIndex >= 0) {
          contentLines.push(line.slice(0, closeIndex))
          nextLine += 1
          break
        }
        contentLines.push(line)
      }
    }

    const token = state.push('math_block', 'math', 0)
    token.block = true
    token.content = contentLines.join('\n').trim()
    token.map = [startLine, nextLine]
    token.markup = '$$'
    state.line = nextLine
    return true
  })

  parser.renderer.rules.math_inline = (tokens, idx) => renderMath(tokens[idx]?.content || '', false)
  parser.renderer.rules.math_block = (tokens, idx) => `${renderMath(tokens[idx]?.content || '', true)}\n`
}

export const createMarkdownRenderer = () =>
  new MarkdownIt({ html: true, linkify: true, breaks: false }).use(mathPlugin)
