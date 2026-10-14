"""Geometry-first local candidates with explicit fallbacks; no domain vocabulary."""
import html
import re
from statistics import median

import pymupdf as fitz

from backend.ocr.common import IMAGE_RE
from backend.ocr.general_parser.response import escape_scripts
from backend.ocr.hybrid_layout import page_sizes, rect_from_layout
from backend.ocr.source_polish.evidence import project, readable

VERSION = 'generic-pilot-v9'


def overlap(outer, inner):
    inner = fitz.Rect(inner)
    return (fitz.Rect(outer) & inner).get_area() / max(inner.get_area(), 1e-9)


def visible(value):
    return project(value.replace('\u200b', '').replace('\ufeff', ''))['text']


def source_rows(page):
    spans = [s for b in page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
             for line in b.get('lines', []) for s in line['spans'] if s['text'].strip()]
    groups = []
    for span in sorted(spans, key=lambda s: (s['origin'][1], s['bbox'][0])):
        anchors = [s for s in spans if span['size'] < .85 * s['size']
                   and -.05 * s['size'] <= span['bbox'][0] - s['bbox'][2] <= .8 * s['size']
                   and .04 * s['size'] < abs(span['origin'][1] - s['origin'][1]) < .65 * s['size']]
        anchors.sort(key=lambda s: abs(span['bbox'][0] - s['bbox'][2]))
        # Competing baselines are ambiguous: leave ordinary text, do not guess a script.
        anchor = anchors[0] if anchors and all(abs(s['origin'][1] - anchors[0]['origin'][1]) < .1 * s['size'] for s in anchors) else None
        baseline = anchor['origin'][1] if anchor else span['origin'][1]
        script = ('sub' if span['origin'][1] > baseline else 'sup') if anchor else None
        group = next((g for g in groups if abs(g['baseline'] - baseline) <= .1 * max(g['size'], span['size'])), None)
        if group is None:
            group = {'baseline': baseline, 'size': span['size'], 'spans': []}
            groups.append(group)
        group['spans'].append((span, script))
        group['size'] = max(group['size'], span['size'])
    rows = []
    for group in groups:
        pieces, rect, plain = [], None, []
        previous = None
        for span, script in sorted(group['spans'], key=lambda item: item[0]['bbox'][0]):
            if previous and span['bbox'][0] - previous['bbox'][2] > .25 * group['size']:
                pieces.append(' ')
                plain.append(' ')
            value = html.escape(span['text'], quote=False)
            pieces.append(f'<{script}>{value}</{script}>' if script else value)
            plain.append(span['text'])
            rect = fitz.Rect(span['bbox']) if rect is None else rect | fitz.Rect(span['bbox'])
            previous = span
        rows.append({'bbox': list(rect), 'text': ''.join(plain), 'markdown': ''.join(pieces),
                     'size': group['size'], 'script_count': sum(bool(k) for _, k in group['spans'])})
    return sorted(rows, key=lambda row: (row['bbox'][1], row['bbox'][0]))


def table_markdown(cells):
    def cell(value):
        return escape_scripts(value).replace('|', '&#124;').replace('\n', '<br>')
    rows = [cells[0], ['---'] * len(cells[0]), *cells[1:]]
    return '\n'.join('| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows)


def merge_vision_regions(blocks):
    """Batch aligned neighboring requests without swallowing partial source blocks."""
    result = list(blocks)
    while True:
        merged = False
        candidates = [b for b in result if b['action'] == 'vision' and b.get('bbox')]
        for i, left in enumerate(candidates):
            a = fitz.Rect(left['bbox'])
            for right in candidates[i + 1:]:
                b = fitz.Rect(right['bbox'])
                gap = max(a.y0, b.y0) - min(a.y1, b.y1)
                if (min(a.width, b.width) < .5 * max(a.width, b.width)
                        or abs(a.x0 - b.x0) > .2 * max(a.width, b.width)
                        or gap > 1.5 * min(a.height, b.height)):
                    continue
                bounds = a | b
                touched = [item for item in result if item.get('bbox') and overlap(bounds, item['bbox']) > 0]
                # Close over crossing source blocks; never crop half an occurrence.
                for _ in range(len(result)):
                    expanded = fitz.Rect(bounds)
                    for item in touched:
                        expanded |= fitz.Rect(item['bbox'])
                    if expanded == bounds:
                        break
                    bounds = expanded
                    touched = [item for item in result if item.get('bbox') and overlap(bounds, item['bbox']) > 0]
                if bounds.get_area() > 2 * (a | b).get_area():
                    continue
                touched.sort(key=lambda item: item['order'])
                joined = {'id': left['id'] + '_' + right['id'], 'kind': 'image', 'bbox': list(bounds),
                          'order': min(item['order'] for item in touched),
                          'source_ids': [value for item in touched for value in item['source_ids']],
                          'raw': '\n\n'.join(item['raw'] for item in touched),
                          'text': '\n\n'.join(item['text'] for item in touched),
                          'native_hint': '\n\n'.join(item['native_hint'] for item in touched if item.get('native_hint')),
                          'markdown': '\n\n'.join(item['markdown'] for item in touched),
                          'action': 'vision', 'reason': 'adjacent_uncertain_regions',
                          'children': [item['id'] for item in touched]}
                if all(item.get('native_markdown') for item in touched):
                    joined['native_markdown'] = '\n\n'.join(item['native_markdown'] for item in touched)
                result = [item for item in result if item not in touched] + [joined]
                merged = True
                break
            if merged:
                break
        if not merged:
            return result


def local_page(page, layouts, status):
    dimensions = page_sizes(status).get(page.number)
    blocks = []
    for ordinal, source in enumerate(layouts):
        block = {'id': f'b{ordinal:03d}', 'source_ids': [source['uniqueId']], 'order': ordinal,
                 'kind': source['type'], 'raw': source.get('markdownContent') or source.get('text', ''),
                 'text': source.get('text', ''), 'action': 'preserve', 'reason': 'provider_content'}
        block['markdown'] = IMAGE_RE.sub('', block['raw']).strip()
        try:
            block['bbox'] = list(rect_from_layout(source, page, dimensions))
        except (TypeError, ValueError, KeyError):
            block.update(bbox=None, action='unresolved', reason='missing_coordinates')
        blocks.append(block)
    rows = source_rows(page)
    image_rects = [fitz.Rect(i['bbox']) & page.rect for i in page.get_image_info()]
    consumed = set()
    replacements = []
    for number, table in enumerate(page.find_tables(strategy='lines').tables):
        cells = table.extract()
        if (not cells or len(cells) < 2 or len(cells[0]) < 2
                or any(len(r) != len(cells[0]) or any(v is None or not readable(v) and v != '' for v in r) for r in cells)):
            continue  # Unsupported merged/undecodable cells must use the image fallback.
        bounds = fitz.Rect(table.bbox) + (-2, -2, 2, 2)
        members = [b for b in blocks if b['bbox'] and overlap(bounds, b['bbox']) >= .9]
        # Do not cut a paragraph or a composite image to replace just a table inside it.
        crossings = [b for b in blocks if b['bbox'] and overlap(bounds, b['bbox']) > .05 and b not in members]
        if not members or crossings or any(b['id'] in consumed for b in members):
            continue
        consumed.update(b['id'] for b in members)
        replacements.append({'id': f't{number:03d}', 'kind': 'table', 'bbox': list(table.bbox),
                             'order': min(b['order'] for b in members), 'source_ids': [i for b in members for i in b['source_ids']],
                             'raw': '\n\n'.join(b['raw'] for b in members), 'text': '',
                             'markdown': table_markdown(cells), 'cells': cells,
                             'action': 'local', 'reason': 'native_table_geometry'})
    blocks = [b for b in blocks if b['id'] not in consumed] + replacements
    for block in blocks:
        if block['action'] != 'preserve' or not block['bbox']:
            continue
        if block['kind'] in {'figure', 'image', 'chart', 'multicolumn'} or IMAGE_RE.search(block['raw']):
            block.update(action='vision', reason='multicolumn_reading_order' if block['kind'] == 'multicolumn' else 'visual_content')
            continue
        if block['kind'] not in {'text', 'title', 'equation', 'formula'}:
            continue
        nearby = [r['size'] for r in rows if overlap(block['bbox'], r['bbox']) > 0]
        padding = .5 * median(nearby) if nearby else 0
        bounds = fitz.Rect(block['bbox']) + (-padding, -padding, padding, padding)
        members = [r for r in rows if overlap(bounds, r['bbox']) >= .9]
        native = '\n'.join(r['text'] for r in members)
        if native and readable(native):
            block['native_markdown'] = '<br>\n'.join(r['markdown'] for r in members)
        same_content = visible(native) == visible(block['markdown'])
        vector_complete = (not any(overlap(image, bounds) > .01 for image in image_rects)
                           and len(visible(native)) >= .95 * len(visible(block['markdown'])))
        if native and readable(native) and (same_content or vector_complete):
            content = '<br>\n'.join(r['markdown'] for r in members)
            prefix = re.match(r'^#{1,6}\s+', block['markdown'])
            block.update(markdown=(prefix[0] if prefix else '') + content, action='local',
                         reason='native_exact_content_geometry' if same_content else 'native_vector_source', script_count=sum(r['script_count'] for r in members))
        elif native and visible(native) != visible(block['markdown']):
            block.update(action='vision', reason='native_provider_disagreement', native_hint=native)
        elif not readable(block['markdown']):
            block.update(action='vision', reason='undecodable_content')
    # A scanned page cannot be audited via its (absent) native text. One full content
    # image replaces its OCR fragments, preserving the source image separately.
    large = [r for r in image_rects if r.get_area() > .6 * page.rect.get_area()]
    if large and sum(len(r['text'].strip()) for r in rows) < 30:
        return [{'id': 'scan', 'kind': 'image', 'bbox': list(page.rect), 'order': 0,
                 'source_ids': [i for b in blocks for i in b['source_ids']],
                 'raw': '\n\n'.join(b['raw'] for b in blocks), 'text': '\n\n'.join(b['text'] for b in blocks),
                 'markdown': '\n\n'.join(b['markdown'] for b in blocks),
                 'action': 'vision', 'reason': 'scan_without_native_evidence'}]
    blocks = merge_vision_regions(blocks)
    # Preserve visual evidence even when the provider has returned only text blocks.
    for n, rect in enumerate(image_rects):
        if rect.get_area() < .02 * page.rect.get_area():
            continue
        if any(b['bbox'] and b['kind'] in {'figure', 'image', 'chart'} and overlap(b['bbox'], rect) >= .9 for b in blocks):
            continue
        blocks.append({'id': f'asset{n}', 'kind': 'asset', 'bbox': list(rect), 'order': len(blocks),
                       'source_ids': [], 'raw': '', 'text': '', 'markdown': '',
                       'action': 'asset', 'reason': 'source_image_retention'})
    if not blocks:
        pix = page.get_pixmap(matrix=fitz.Matrix(.5, .5), colorspace=fitz.csGRAY, alpha=False)
        ink = sum(v < 245 for v in pix.samples) / len(pix.samples)
        if ink > .001:
            blocks.append({'id': 'missing', 'kind': 'image', 'bbox': list(page.rect), 'order': 0,
                           'source_ids': [], 'raw': '', 'text': '', 'markdown': '',
                           'action': 'vision', 'reason': 'missing_nonblank_page'})
    return sorted(blocks, key=lambda b: b['order'])
