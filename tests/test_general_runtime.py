import json
from pathlib import Path

import pymupdf as fitz
import pytest

from backend.ocr.general_parser import convert as runtime
from backend.ocr.general_parser.cloud import Cloud


@pytest.fixture
def config():
    return {'bailian': {'api_key': 'test', 'base_url': 'https://invalid.example', 'timeout_seconds': 1},
            'image_description': {'model': 'qwen3.8-flash'}, 'hybrid': {'dpi': 120, 'max_pixels': 1000000},
            'pricing': {'description_input_per_million_cny': .8, 'description_output_per_million_cny': 2.7}}


def test_runtime_whole_document_uses_source_geometry_and_reuses_base_cache(tmp_path, monkeypatch, config):
    pdf = tmp_path / 'report.pdf'
    with fitz.open() as doc:
        doc.new_page(width=200, height=300).insert_text((20, 40), 'XYZ 41')
        doc.new_page(width=200, height=300)
        doc.save(pdf)
    submissions = []
    def base(pdf, directory, config, **kwargs):
        submissions.append(kwargs)
        (directory / 'status.json').write_text(json.dumps({'OutputFormatResult': [{'OutputType': 'pageImage',
            'Pages': [{'PageIdCurDoc': 0, 'ImageWidth': 200, 'ImageHeight': 300}, {'PageIdCurDoc': 1, 'ImageWidth': 200, 'ImageHeight': 300}]}]}))
        (directory / 'layouts.json').write_text(json.dumps([{'pageNum': 0, 'uniqueId': 'a', 'type': 'text',
            'text': 'XYZ 41', 'markdownContent': 'XYZ 41', 'pos': [{'x': 15, 'y': 20}, {'x': 100, 'y': 50}]}]))
        return {'status': 'complete'}
    monkeypatch.setattr(runtime, 'parse_docmind', base)
    monkeypatch.setattr(Cloud, 'call', lambda *args: pytest.fail('Native/blank pages should not require image API'))
    output = tmp_path / 'work/output'
    progress = []
    runtime.convert(pdf, output, config, lambda **p: progress.append(p))
    runtime.convert(pdf, output, config, lambda **p: progress.append(p))
    assert len(submissions) == 1 and submissions[0]['enhanced'] is False
    assert 'XYZ 41' in (output / 'report.md').read_text()
    assert (output / 'report_text_only.md').exists()
    assert progress[-1]['pages_done'] == 2
    assert json.loads((output / 'conversion.json').read_text())['vision_regions'] == 0


def test_unknown_base_submission_is_not_automatically_resubmitted(tmp_path, monkeypatch, config):
    pdf = tmp_path / 'report.pdf'
    with fitz.open() as doc:
        doc.new_page()
        doc.save(pdf)
    cache = tmp_path / 'work/base_ocr'
    cache.mkdir(parents=True)
    (cache / 'submit_started.json').write_text('{}')
    monkeypatch.setattr(runtime, 'parse_docmind', lambda *a, **k: pytest.fail('Must not resubmit unknown paid work'))
    with pytest.raises(ValueError, match='避免重复计费'):
        runtime.convert(pdf, tmp_path / 'work/output', config, lambda **p: None)


def test_explicit_retry_archives_charges_and_keeps_successes(tmp_path, monkeypatch, config):
    calls = []
    def request(settings, data, target, *args):
        calls.append(data)
        if data == b'bad' and len(calls) == 1:
            return {'status': 'failed', 'estimated_cny': None}
        target.write_text('{"items":[{"type":"text","text":"x","uncertain":""}]}')
        return {'status': 'complete', 'estimated_cny': .01, 'finish_reason': 'stop'}
    monkeypatch.setattr('backend.ocr.general_parser.cloud.vision_request', request)
    image = tmp_path / 'image.png'
    cloud = Cloud(config, tmp_path / 'cache')
    try:
        image.write_bytes(b'bad'); cloud.call(image)
        image.write_bytes(b'good'); cloud.call(image)
        cloud.prepare_retry()
        image.write_bytes(b'good'); assert cloud.call(image)['reused']
        image.write_bytes(b'bad'); assert cloud.call(image)['status'] == 'complete'
        assert len(calls) == 3
        assert len(cloud.ledger) == 3
        assert sum(row.get('estimated_cny') is None for row in cloud.ledger.values()) == 1
    finally:
        cloud.close()
