import json
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend.app import alignment, case_catalog, case_imports, main, services, storage


@pytest.fixture
def env(tmp_path, monkeypatch):
    case, criteria, jobs = tmp_path / 'case', tmp_path / 'criteria', tmp_path / 'jobs'
    case.mkdir()
    criteria.mkdir()
    for module in (case_catalog, case_imports, main, services, alignment):
        monkeypatch.setattr(module, 'CASE_ROOT', case)
    for module in (case_imports, main, services, storage, alignment):
        monkeypatch.setattr(module, 'CRITERIA_DIR', criteria)
    monkeypatch.setattr(storage, 'RESULTS_DIR', tmp_path / 'results')
    monkeypatch.setattr(case_imports, 'JOBS_ROOT', jobs)
    monkeypatch.setattr(case_imports, 'start_job', lambda _: None)
    monkeypatch.setattr(case_imports, 'parser_command', lambda *args: ['not-executed'])
    return TestClient(main.app), case, criteria, jobs


def pdf_bytes():
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 60), 'sample report')
        return doc.tobytes()


def upload(client, name='我的实验', paths=None, content=None):
    paths = paths or ['folder/a.pdf', 'folder/nested/a.PDF']
    return client.post('/case-imports', data={'name': name, 'paths': json.dumps(paths)},
                       files=[('files', (Path(p).name, content or pdf_bytes(), 'application/pdf')) for p in paths])


def fake_converter(job, item, work, notify):
    notify({'stage': '保存Markdown', 'pages_done': item['pages']})
    output = work / 'output'
    output.mkdir(exist_ok=True)
    for suffix in ('', '_text_only'):
        (output / f"{item['report_name']}{suffix}.md").write_text('## test\n\nevidence')
    return output


def test_upload_parse_case_and_isolated_rubric(env, monkeypatch):
    client, root, criteria, _ = env
    response = upload(client)
    assert response.status_code == 200, response.text
    job = response.json()['data']
    assert job['engine'] == 'hybrid'
    assert len({f['report_name'].casefold() for f in job['files']}) == 2
    assert client.get('/cases').json()['data']['items'] == ['我的实验']
    assert client.get('/cases/我的实验/reports').json()['data']['items'] == []
    first_rubric = client.get('/criteria', params={'case_id': '我的实验'}).json()['data']['items'][0]
    assert client.get(f'/criteria/{first_rubric}').json()['data']['item']['criteria'] == []
    monkeypatch.setattr(case_imports, 'convert_file', fake_converter)
    case_imports.run_job(job['id'])
    saved = client.get(f"/case-imports/{job['id']}").json()['data']
    assert saved['status'] == 'completed'
    assert all(f['pages_done'] == 1 for f in saved['files'])
    assert any('保存Markdown' in e['message'] for e in saved['events'])
    reports = client.get('/cases/我的实验/reports').json()['data']['items']
    assert len(reports) == 2
    assert client.get(f"/cases/我的实验/reports/{reports[0]['name']}").json()['data']['markdown'].startswith('## test')
    other = upload(client, '另一组').json()['data']
    payload = {'schema_version': 'structured-rubric-v1', 'title': '共同名称', 'background': '', 'language': 'zh', 'base_score': 100, 'criteria': []}
    a = client.post('/criteria', json={'file_name': '评分标准.json', 'case_id': '我的实验', 'data': payload})
    b = client.post('/criteria', json={'file_name': '评分标准.json', 'case_id': other['case_id'], 'data': payload})
    assert a.status_code == b.status_code == 200
    assert a.json()['data']['file_name'] != b.json()['data']['file_name']
    assert client.get('/criteria', params={'case_id': '我的实验'}).json()['data']['items'][0] == a.json()['data']['file_name']


@pytest.mark.parametrize('name,paths', [('../bad', ['folder/a.pdf']), ('x', ['../a.pdf']), ('x', ['/a.pdf']), ('x', ['folder/a.txt'])])
def test_rejects_paths_without_creating_case(env, name, paths):
    client, root, _, _ = env
    assert upload(client, name, paths).status_code == 400
    assert not list(root.iterdir())


def test_duplicate_case_and_invalid_pdf_never_overwrite(env):
    client, root, _, _ = env
    assert upload(client).status_code == 200
    before = (root / '我的实验/case.json').read_bytes()
    assert upload(client).status_code == 409
    assert (root / '我的实验/case.json').read_bytes() == before
    assert upload(client, '坏文件', content=b'not PDF').status_code == 400
    assert not (root / '坏文件').exists()


def test_partial_failure_retry_only_unfinished_and_persistent_history(env, monkeypatch):
    client, _, _, _ = env
    job = upload(client).json()['data']
    calls = []
    def convert(job, item, work, notify):
        calls.append(item['id'])
        if item['id'] == '0001' and item['attempts'] == 1:
            raise ValueError('模拟OCR失败')
        return fake_converter(job, item, work, notify)
    monkeypatch.setattr(case_imports, 'convert_file', convert)
    case_imports.run_job(job['id'])
    assert case_imports.read_job(job['id'])['status'] == 'partial'
    assert len(services.list_case_reports('我的实验')) == 1
    assert client.post(f"/case-imports/{job['id']}/retry").status_code == 200
    case_imports.run_job(job['id'])
    assert calls == ['0000', '0001', '0001']
    assert case_imports.read_job(job['id'])['status'] == 'completed'
    assert len(client.get('/case-imports').json()['data']['items']) == 1


def test_restart_preserves_completed_reports_and_marks_pending(env, monkeypatch):
    client, _, _, _ = env
    job = upload(client).json()['data']
    job['status'] = 'running'
    job['files'][0]['status'] = 'completed'
    job['files'][1]['status'] = 'running'
    case_imports.save_job(job)
    case_imports.recover_imports()
    restored = case_imports.read_job(job['id'])
    assert restored['status'] == 'interrupted'
    assert restored['files'][0]['status'] == 'completed'
    assert restored['files'][1]['status'] == 'interrupted'


def test_parser_rejects_incomplete_publication(env, monkeypatch, tmp_path):
    # Actual worker publication path is tested separately from a stub parser.
    client, _, _, _ = env
    job = upload(client, paths=['folder/a.pdf']).json()['data']
    class Process:
        returncode = 0
        def poll(self): return 0
    monkeypatch.setattr(case_imports.subprocess, 'Popen', lambda *a, **k: Process())
    case_imports.run_job(job['id'])
    assert case_imports.read_job(job['id'])['status'] == 'failed'
    assert services.list_case_reports('我的实验') == []


def test_recovery_leaves_active_worker_alone(env):
    client, _, _, _ = env
    job = upload(client).json()['data']
    job['status'] = 'running'
    case_imports.save_job(job)
    with case_imports.job_lock(job['id']) as lock:
        assert lock is not None
        case_imports.recover_imports()
        assert case_imports.read_job(job['id'])['status'] == 'running'
    case_imports.recover_imports()
    assert case_imports.read_job(job['id'])['status'] == 'interrupted'


def test_published_report_survives_crash_before_job_status_save(env, monkeypatch):
    client, root, _, _ = env
    job = upload(client, paths=['folder/a.pdf']).json()['data']
    monkeypatch.setattr(case_imports, 'convert_file', fake_converter)
    case_imports.run_job(job['id'])
    # Simulate the persisted job lagging behind an already published report.
    job['status'] = 'interrupted'
    job['files'][0]['status'] = 'running'
    case_imports.save_job(job)
    def unexpected_conversion(*args):
        pytest.fail('Published report must not trigger another conversion')
    monkeypatch.setattr(case_imports, 'convert_file', unexpected_conversion)
    case_imports.run_job(job['id'])
    assert case_imports.read_job(job['id'])['status'] == 'completed'
    assert len(services.list_case_reports('我的实验')) == 1


def test_imported_report_can_be_graded_with_its_own_rubric(env, monkeypatch):
    client, root, _, _ = env
    job = upload(client, paths=['folder/a.pdf']).json()['data']
    monkeypatch.setattr(case_imports, 'convert_file', fake_converter)
    case_imports.run_job(job['id'])
    rubric = {'schema_version': 'structured-rubric-v1', 'title': '实验评分',
              'background': '', 'language': 'zh', 'base_score': 100, 'criteria': [{
                  'criteria_id': '1', 'criteria_name': 'Accuracy', 'description': 'Accuracy',
                  'original_description': 'Accuracy', 'max_score': 5, 'section': 'Methods',
                  'few_shot_examples': [], 'scoring_points': [{
                      'point_id': '1.p1', 'summary': 'Accuracy', 'deduction_rules': [{
                          'rule_id': '1.p1.r1', 'condition': 'The method is inaccurate', 'deduct': 5}],
                      'max_deduction': 5, 'aggregation': 'max_triggered'}],
                  'scoring_aggregation': {'mode': 'sum_deductions_capped', 'cap': 5}}]}
    saved = client.post('/criteria', json={'file_name': '评分.json', 'case_id': job['case_id'], 'data': rubric})
    assert saved.status_code == 200, saved.text
    file_name = saved.json()['data']['file_name']
    response = client.post(f"/cases/{job['case_id']}/reports/a/grade", json={
        'criteria_id': '1', 'criteria_score': 3, 'score_reason': 'teacher decision',
        'anchor_text': 'evidence', 'scoring_point_scores': [{'point_id': '1.p1', 'score': 3}],
        'criteria_file_name': file_name})
    assert response.status_code == 200, response.text
    events = json.loads((root / job['case_id'] / 'alignment/events.json').read_text())
    assert events[-1]['authored_grade']['criteria_score'] == 3
