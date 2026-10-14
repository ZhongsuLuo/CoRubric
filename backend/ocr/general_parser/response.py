"""One ordered item per source occurrence; shared inert rendering for all fields."""
import html
import json


def escape_scripts(text):
    text = html.escape(text, quote=False)
    for tag in ('sup', 'sub'):
        text = text.replace(f'&lt;{tag}&gt;', f'<{tag}>').replace(f'&lt;/{tag}&gt;', f'</{tag}>')
    return text


def validate_items(text):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    data = json.loads(text)
    # Some compatible endpoints return the item array without the requested
    # envelope. Wrapping it is lossless; every item still passes validation below.
    if isinstance(data, list):
        data = {'items': data}
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('MissingItems')
    for item in data['items']:
        if not isinstance(item, dict) or item.get('type') not in {'text', 'table', 'formula', 'figure'}:
            raise ValueError('InvalidItemType')
        bbox = item.get('bbox')
        if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(v) not in (int, float) for v in bbox)
                or not 0 <= bbox[0] < bbox[2] <= 1000 or not 0 <= bbox[1] < bbox[3] <= 1000):
            raise ValueError('InvalidItemCoordinates')
        if not isinstance(item.get('uncertain'), str):
            raise ValueError('MissingUncertainty')
        if item['type'] == 'table':
            headers, rows = item.get('headers'), item.get('rows')
            if (not isinstance(headers, list) or not headers or not all(isinstance(v, str) for v in headers)
                    or not isinstance(rows, list) or not rows
                    or any(not isinstance(r, list) or len(r) != len(headers) or not all(isinstance(v, str) for v in r) for r in rows)):
                raise ValueError('InvalidTable')
        elif not isinstance(item.get('text'), str) or not item['text'].strip():
            raise ValueError('EmptyItem')
    return data


def render_items(data, table_renderer):
    parts = []
    for item in data['items']:
        if item['uncertain']:
            parts.append('> 待核验：' + html.escape(item['uncertain']).replace('\n', ' '))
        if item['type'] == 'table':
            parts.append(table_renderer([item['headers'], *item['rows']]))
        elif item['type'] == 'formula':
            parts.append('$$\n' + html.escape(item['text'], quote=False) + '\n$$')
        elif item['type'] == 'figure':
            parts.append('> 图片描述（模型生成）：' + html.escape(item['text']).replace('\n', ' '))
        else:
            parts.append(escape_scripts(item['text']).replace('\n', '  \n'))
    return '\n\n'.join(parts)
