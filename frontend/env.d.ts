/// <reference types="vite/client" />

declare module 'markdown-it-katex' {
	import type { PluginSimple } from 'markdown-it'
	const plugin: PluginSimple
	export default plugin
}
