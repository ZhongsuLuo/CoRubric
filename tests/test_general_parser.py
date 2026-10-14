import json
from pathlib import Path

import pymupdf as fitz
import pytest

from backend.ocr.general_parser.core import source_rows, table_markdown, local_page
from backend.ocr.general_parser.cloud import Cloud


def test_scripts_across_lines_survive_scale_and_unknown_labels():
    for scale in (1, 2):
        doc = fitz.open()
        page = doc.new_page(width=600 * scale, height=400 * scale)
        page.insert_text((50 * scale, 80 * scale), 'ZX', fontsize=12 * scale)
        end = 50 + fitz.get_text_length('ZX', fontsize=12)
        page.insert_text((end * scale, 75 * scale), '7-', fontsize=6 * scale)
        page.insert_text((50 * scale, 110 * scale), 'ZX', fontsize=12 * scale)
        rows = source_rows(page)
        assert sum('<sup>7-</sup>' in r['markdown'] for r in rows) == 1
        assert sum('ZX' in r['text'] for r in rows) == 2
        doc.close()


def test_blank_and_vector_only_page_are_distinct():
    doc = fitz.open()
    page = doc.new_page()
    assert local_page(page, [], {}) == []
    page.draw_rect(fitz.Rect(50, 50, 350, 350), color=(0, 0, 0), fill=(0, 0, 0))
    blocks = local_page(page, [], {})
    assert blocks[0]['reason'] == 'missing_nonblank_page'
    doc.close()


def test_table_preserves_negative_zero_and_duplicate_rows():
    text = table_markdown([['arbitrary|key', 'value'], ['same', '-0.000'], ['same', '-0.000']])
    assert text.count('-0.000') == 2
    assert 'arbitrary&#124;key' in text


def test_budget_keeps_unknown_charge_and_never_resubmits(tmp_path, monkeypatch):
    config = {'bailian': {'api_key': 'test', 'base_url': 'https://invalid.example', 'timeout_seconds': 1},
              'image_description': {'model': 'qwen3.8-flash'},
              'pricing': {'description_input_per_million_cny': .8, 'description_output_per_million_cny': 2.7}}
    called = []
    def unknown(*args):
        called.append(1)
        return {'status': 'failed', 'estimated_cny': None, 'api_requests': 1}
    monkeypatch.setattr('backend.ocr.general_parser.cloud.vision_request', unknown)
    image = tmp_path / 'image.png'
    cloud = Cloud(config, tmp_path / 'cache')
    try:
        for n in range(10):
            image.write_bytes(str(n).encode())
            result = cloud.call(image)
        assert len(called) == 9
        assert result['reason'] == 'run_budget_exceeded'
    finally:
        cloud.close()
    cloud = Cloud(config, tmp_path / 'cache')
    try:
        image.write_bytes(b'0')
        assert cloud.call(image)['reused']
        assert len(called) == 9
    finally:
        cloud.close()


def test_geometry_batching_preserves_each_source_occurrence():
    from backend.ocr.general_parser.core import merge_vision_regions
    def block(i, box):
        return {'id': str(i), 'bbox': box, 'action': 'vision', 'order': i,
                'source_ids': [str(i)], 'raw': 'repeat', 'text': 'repeat', 'markdown': 'repeat'}
    blocks = [block(0, [10, 10, 100, 20]), block(1, [10, 25, 100, 35]),
              block(2, [200, 10, 290, 20])]
    result = merge_vision_regions(blocks)
    assert len(result) == 2
    assert sorted(i for b in result for i in b['source_ids']) == ['0', '1', '2']
    assert sum(b['markdown'].count('repeat') for b in result) == 3


def test_zero_width_formatting_is_not_a_content_conflict():
    from backend.ocr.general_parser.core import visible
    assert visible('陌生字段\u200b') == visible('陌生字段')
    assert visible('5.00') != visible('5.0')


def test_merge_owns_crossing_blocks_and_preserves_native_hints():
    from backend.ocr.general_parser.core import merge_vision_regions
    blocks = [{'id': str(i), 'order': i, 'source_ids': [str(i)], 'bbox': box,
               'action': 'vision' if i < 2 else 'local', 'raw': 'x', 'text': 'x',
               'markdown': 'x', 'native_hint': str(i)}
              for i, box in enumerate(([10, 10, 100, 20], [10, 25, 100, 35], [95, 15, 120, 30]))]
    result = merge_vision_regions(blocks)
    assert len(result) == 1
    assert result[0]['bbox'] == [10, 10, 120, 35]
    assert result[0]['source_ids'] == ['0', '1', '2']
    assert result[0]['native_hint'] == '0\n\n1\n\n2'


@pytest.mark.parametrize('change', [
    {'bbox': [0, 0, 10]}, {'bbox': [0, 0, True, 10]}, {'bbox': [10, 0, 0, 10]},
    {'bbox': [0, 0, float('nan'), 10]}, {'uncertain': None}, {'type': 'unknown'},
    {'type': 'table', 'headers': ['one', 'two'], 'rows': [['one']]},
])
def test_invalid_structured_output_is_rejected(change):
    from backend.ocr.general_parser.response import validate_items
    item = {'type': 'text', 'bbox': [0, 0, 20, 20], 'text': 'x', 'uncertain': ''}
    with pytest.raises(ValueError):
        validate_items(json.dumps({'items': [{**item, **change}]}))


def test_ordered_output_preserves_occurrences_and_only_inert_script_tags():
    from backend.ocr.general_parser.response import validate_items, render_items
    items = [{'type': 'text', 'bbox': [0, y, 20, y+10], 'text': 'same', 'uncertain': ''} for y in (0, 20)]
    items.append({'type': 'table', 'bbox': [0, 40, 20, 60], 'headers': ['X<sup>7-</sup>'],
                  'rows': [['<img src=x>|-0.000']], 'uncertain': '字形不清'})
    output = render_items(validate_items(json.dumps({'items': items})), table_markdown)
    assert output.count('same') == 2
    assert '<sup>7-</sup>' in output and '<img' not in output
    assert '&#124;-0.000' in output and '待核验' in output


def test_source_geometry_requires_full_character_consensus():
    from backend.ocr.general_parser.rendering import assemble
    item = {'type': 'text', 'text': 'XYZ<sub>0</sub>+Q<sub>7</sub>', 'uncertain': ''}
    block = {'action': 'vision', 'markdown': 'bad OCR', 'native_markdown': 'XYZ0+Q<sub>7</sub>',
             'result': {'status': 'complete', 'data': {'items': [item]}}}
    assert assemble([block], vision=True).strip() == 'XYZ0+Q<sub>7</sub>'
    item['text'] = 'XYZ<sub>1</sub>+Q<sub>7</sub>'
    assert 'XYZ<sub>1</sub>' in assemble([block], vision=True)
    item.update(text='XYZ<sub>0</sub>+Q<sub>7</sub>', uncertain='字形不清')
    assert '待核验' in assemble([block], vision=True)
    assert 'XYZ<sub>0</sub>' in assemble([block], vision=True)


def test_array_envelope_normalization_is_lossless_and_still_validated():
    from backend.ocr.general_parser.response import validate_items
    items = [{'type': 'text', 'text': '任意字符 -0.000', 'headers': [], 'rows': [], 'uncertain': ''}]
    assert validate_items(json.dumps(items)) == {'items': items}
    items[0]['uncertain'] = None
    with pytest.raises(ValueError):
        validate_items(json.dumps(items))


def test_cached_envelope_failure_revalidates_without_network(tmp_path, monkeypatch):
    config = {'bailian': {'api_key': 'test', 'base_url': 'https://invalid.example', 'timeout_seconds': 1},
              'image_description': {'model': 'qwen3.8-flash'},
              'pricing': {'description_input_per_million_cny': .8, 'description_output_per_million_cny': 2.7}}
    image = tmp_path / 'image.png'
    image.write_bytes(b'not sent')
    calls = []
    def request(settings, image_bytes, target, *args):
        calls.append(1)
        target.write_text('[{"type":"text","text":"x","uncertain":""}]')
        return {'status': 'complete', 'finish_reason': 'stop', 'estimated_cny': .001, 'api_requests': 1}
    monkeypatch.setattr('backend.ocr.general_parser.cloud.vision_request', request)
    cloud = Cloud(config, tmp_path / 'cache')
    try:
        row = cloud.call(image)
        cloud.ledger[row['key']].update(status='partial', reason='invalid_response')
        result = cloud.call(image)
        assert result['status'] == 'complete' and result['reused']
        assert result['reason'] == 'cached_response_revalidated' and len(calls) == 1
        assert cloud.ledger[row['key']]['status'] == 'partial'  # historical ledger stays unchanged
    finally:
        cloud.close()


def test_missing_coordinates_retains_page_and_model_markup_is_escaped():
    from backend.ocr.general_parser.rendering import assemble, render_model
    output = assemble([{'action': 'unresolved', 'markdown': 'text'}])
    assert 'source.png' in output and 'text' in output
    rendered = render_model({'transcription': '<script>bad</script><sup>2</sup>',
                             'formulas': ['<img src=x>'], 'tables': [], 'parameters': [], 'description': ''})
    assert '<script>' not in rendered and '<img' not in rendered
    assert '<sup>2</sup>' in rendered
