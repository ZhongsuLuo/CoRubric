"""Folder UI test server: real uploads/jobs/catalog, deterministic conversion only."""
import base64
import time
from tests.e2e_app import main
from backend.app import case_imports

app = main.app


def convert_fixture(job, item, work, notify):
    notify({'stage': '基础OCR', 'pages_done': 0})
    time.sleep(.3)
    output = work / 'output'
    (output / 'images').mkdir(parents=True, exist_ok=True)
    (output / 'images/chart.png').write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1V0AAAAASUVORK5CYII='))
    text = '# Imported experiment\n\nEvidence from the imported PDF.\n\n![Source chart](images/chart.png)\n\n> 图片描述（模型生成）：测试图示'
    for suffix in ('', '_text_only'):
        (output / f"{item['report_name']}{suffix}.md").write_text(text)
    notify({'stage': '保存Markdown', 'pages_done': item['pages']})
    return output


case_imports.convert_file = convert_fixture
