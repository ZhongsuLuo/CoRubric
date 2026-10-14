declare module 'katex' {
  export type KatexOptions = {
    displayMode?: boolean
    throwOnError?: boolean
    strict?: boolean | string | ((errorCode: string, errorMsg: string, token: unknown) => boolean | string)
  }

  const katex: {
    renderToString(source: string, options?: KatexOptions): string
  }

  export default katex
}
