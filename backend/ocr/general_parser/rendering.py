"""Shared source-aware Markdown assembly for both runtime and evaluation."""
import html
from backend.ocr.general_parser.core import table_markdown, visible
from backend.ocr.general_parser.response import escape_scripts, render_items


def render_model(data):
    if 'items' in data:
        return render_items(data, table_markdown)
    # Allow only the two inert script tags from the transcription.
    text = escape_scripts(data['transcription'])
    parts = [text.replace('\n', '  \n')]
    parts.extend('$$\n' + html.escape(f, quote=False) + '\n$$' for f in data['formulas'])
    parts.extend(table_markdown([t['headers'], *t['rows']]) for t in data['tables'])
    parts.extend(html.escape(p['label'] + '：' + p['value']) for p in data['parameters'])
    if data['description']:
        parts.append('> 图片描述（模型生成）：' + html.escape(data['description']).replace('\n', ' '))
    return '\n\n'.join(p for p in parts if p)


def assemble(blocks, vision=False):
    parts = []
    for block in blocks:
        content = block['markdown']
        if block.get('image'):
            parts.append(f"![源图]({block['image']})")
        if block['action'] == 'unresolved':
            parts.append(f"![源页]({block.get('source_image', 'source.png')})\n\n> 待处理：源区域坐标不可用，保留源页。")
        if block['action'] == 'vision':
            result = block.get('result', {})
            if vision and result.get('status') == 'complete' and not result['data'].get('uncertainties', []):
                content = render_model(result['data'])
                native = block.get('native_markdown')
                # Both character streams must agree before
                # restoring source geometry; never infer scripts from domain knowledge.
                certain = not any(item.get('uncertain') for item in result['data'].get('items', []))
                if native and certain and visible(native) == visible(content):
                    content = native
            else:
                parts.append('> 待处理：该区域尚未通过转写检查。')
        if content:
            parts.append(content)
    return '\n\n'.join(parts) + '\n'

