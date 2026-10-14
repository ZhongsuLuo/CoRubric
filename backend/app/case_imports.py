"""Folder import jobs. Publish a report only after conversion has finished."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pymupdf as fitz
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .config import API_DATA_DIR, CASE_ROOT, CRITERIA_DIR, PROJECT_ROOT
from .case_catalog import atomic_json, safe_name

router = APIRouter(prefix='/case-imports', tags=['case-imports'])
JOBS_ROOT = Path(API_DATA_DIR) / 'case_imports'
PARSER_ROOT = PROJECT_ROOT / 'backend/ocr'
_LOCK = threading.RLock()
_ACTIVE: set[str] = set()
_WORKER = threading.local()
TERMINAL = {'completed', 'partial', 'failed', 'interrupted'}
MAX_FILES = 200
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def job_path(job_id):
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('无效任务编号')
    return JOBS_ROOT / job_id / 'job.json'


@contextmanager
def job_lock(job_id):
    # The enhanced parser already requires POSIX file locks. This also prevents
    # multiple API workers from parsing the same paid job concurrently.
    import fcntl
    path = job_path(job_id).parent / 'worker.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        try:
            yield lock
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_job(job_id):
    return json.loads(job_path(job_id).read_text(encoding='utf-8'))


def save_job(job, message=None):
    job['updated_at'] = now()
    if message:
        job['events'].append({'time': job['updated_at'], 'message': message})
    atomic_json(job_path(job['id']), job)


def parser_command(source, output, progress, engine):
    if engine == 'legacy':
        return [sys.executable, '-m', 'backend.app.import_legacy', str(source), str(output)]
    python = Path(os.getenv('GRADING_PARSER_PYTHON', sys.executable))
    config = Path(os.getenv('GRADING_PARSER_CONFIG', str(PARSER_ROOT / 'config.local.toml')))
    if not python.is_file() or not config.is_file():
        raise ValueError('解析环境未配置：请设置 GRADING_PARSER_PYTHON 和 GRADING_PARSER_CONFIG')
    return [str(python), '-m', 'backend.ocr.general_parser.convert', '--pdf', str(source),
            '--output', str(output), '--progress', str(progress), '--config', str(config)]


def convert_file(job, item, work, notify):
    source = Path(CASE_ROOT) / job['case_id'] / 'source' / f"{item['report_name']}.pdf"
    output = work / 'output'
    output.mkdir(parents=True, exist_ok=True)
    progress = work / 'progress.json'
    progress.unlink(missing_ok=True)
    command = parser_command(source, output, progress, job['engine'])
    if job['engine'] == 'hybrid' and item.get('attempts', 0) > 1:
        command.append('--retry')
    with (work / 'worker.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=log, stderr=log,
                                   pass_fds=(getattr(_WORKER, 'lock_fd'),) if hasattr(_WORKER, 'lock_fd') else ())
        last = None
        try:
            while process.poll() is None:
                if progress.exists():
                    current = json.loads(progress.read_text())
                    if current != last:
                        notify(current)
                        last = current
                time.sleep(.5)
            if process.returncode:
                detail = json.loads(progress.read_text()) if progress.exists() else {}
                raise ValueError(detail.get('error') or '解析失败，可重试；详细信息已保存在任务日志中')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    name = item['report_name']
    if not (output / f'{name}.md').is_file() or not (output / f'{name}_text_only.md').is_file():
        raise ValueError('解析未生成完整 Markdown，未加入评分列表')
    if list(output.glob('*.partial*')):
        raise ValueError('解析存在未完成页面，未加入评分列表')
    return output


def run_job(job_id):
    try:
        with job_lock(job_id) as lock:
            if lock is not None:
                _WORKER.lock_fd = lock.fileno()
                _run_job(job_id)
    finally:
        if hasattr(_WORKER, 'lock_fd'):
            del _WORKER.lock_fd
        with _LOCK:
            _ACTIVE.discard(job_id)


def _run_job(job_id):
    try:
        with _LOCK:
            job = read_job(job_id)
            job['status'] = 'running'
            save_job(job, '开始解析')
        for item in job['files']:
            destination = Path(CASE_ROOT) / job['case_id'] / 'markdown' / item['report_name']
            marker = destination / '.import-complete.json'
            if marker.exists() and json.loads(marker.read_text()).get('job_id') == job_id:
                item.update(status='completed', stage='完成', pages_done=item['pages'])
            if item['status'] == 'completed':
                continue
            item.update(status='running', stage='准备解析', error=None, pages_done=0)
            item['attempts'] = item.get('attempts', 0) + 1
            save_job(job, f"开始：{item['path']}")
            work = JOBS_ROOT / job_id / item['id']
            work.mkdir(exist_ok=True)
            def notify(state):
                stage = state.get('stage', '解析中')
                changed = stage != item['stage']
                item.update(stage=stage, pages_done=state.get('pages_done', 0))
                save_job(job, f"{item['path']}：{stage}" if changed else None)
            try:
                output = convert_file(job, item, work, notify)
                destination.parent.mkdir(exist_ok=True)
                # Same-filesystem staging makes publication atomic and excludes partial files.
                staging = destination.parent / f".import-{item['id']}"
                if staging.exists():
                    shutil.rmtree(staging)
                shutil.copytree(output, staging)
                atomic_json(staging / '.import-complete.json', {'job_id': job_id, 'file_id': item['id']})
                os.rename(staging, destination)
                item.update(status='completed', stage='完成', pages_done=item['pages'])
                save_job(job, f"完成：{item['path']}")
            except Exception as exc:
                item.update(status='failed', stage='失败', error=str(exc) if isinstance(exc, ValueError) else f'解析失败（{type(exc).__name__}）')
                save_job(job, f"失败：{item['path']} — {item['error']}")
        successes = sum(f['status'] == 'completed' for f in job['files'])
        job['status'] = 'completed' if successes == len(job['files']) else 'partial' if successes else 'failed'
        save_job(job, f"任务结束：成功 {successes}/{len(job['files'])}")
    except Exception as exc:
        job = read_job(job_id)
        job['status'] = 'interrupted'
        save_job(job, f'任务中断（{type(exc).__name__}），可重试未完成文件')


def start_job(job_id):
    with _LOCK:
        if job_id in _ACTIVE:
            return
        _ACTIVE.add(job_id)
        threading.Thread(target=run_job, args=(job_id,), daemon=True, name=f'pdf-import:{job_id}').start()


def recover_imports(include_queued=True):
    # A surviving child inherits the lock; never mark its request abandoned.
    for path in JOBS_ROOT.glob('*/job.json'):
        with job_lock(path.parent.name) as lock:
            if lock is None:
                continue
            job = json.loads(path.read_text())
            if job['status'] == 'running' or include_queued and job['status'] == 'queued':
                job['status'] = 'interrupted'
                for item in job['files']:
                    if item['status'] in {'pending', 'running'}:
                        item.update(status='interrupted', stage='服务重启，等待重试')
                save_job(job, '服务重启，进度已保留；可重试未完成文件')


def create_import(name, files, paths, engine):
    name = safe_name(name.strip())
    if engine not in {'hybrid', 'legacy'}:
        raise ValueError('不支持的解析方式')
    if not files or len(files) > MAX_FILES or len(paths) != len(files):
        raise ValueError(f'请选择1至{MAX_FILES}个PDF文件')
    parser_command(Path('source'), Path('output'), Path('progress'), engine)
    relatives = []
    for value in paths:
        p = PurePosixPath(value)
        if p.is_absolute() or '..' in p.parts or '\\' in value or not p.parts or p.suffix.lower() != '.pdf':
            raise ValueError('文件路径无效或不是PDF')
        relatives.append(value)
    if len(set(relatives)) != len(relatives):
        raise ValueError('上传列表包含重复路径')
    case = Path(CASE_ROOT) / name
    case.parent.mkdir(parents=True, exist_ok=True)
    case.mkdir()  # Atomic reservation; an existing case is never overwritten.
    job_id = uuid.uuid4().hex
    job = {'id': job_id, 'case_id': name, 'engine': engine, 'status': 'queued',
           'created_at': now(), 'updated_at': now(), 'files': [], 'events': []}
    criteria_file = f'import_{job_id}.json'
    try:
        (case / 'source').mkdir()
        used = set()
        total = 0
        for index, (upload, relative) in enumerate(zip(files, relatives)):
            stem = PurePosixPath(relative).stem.strip()
            stem = ''.join(c for c in stem if c not in '/\\:' and ord(c) >= 32).strip('. ')[:50] or 'report'
            name_key = stem.casefold()
            if name_key in used or stem.endswith('_text_only'):
                stem += '__' + hashlib.sha256(relative.encode()).hexdigest()[:10]
            used.add(stem.casefold())
            item_id = f'{index:04d}'
            target = case / 'source' / f'{stem}.pdf'
            size = 0
            with target.open('xb') as stream:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise ValueError('单个PDF不能超过100MB，文件夹总计不能超过500MB')
                    stream.write(chunk)
            with fitz.open(target) as doc:
                if not doc.is_pdf or doc.needs_pass or not len(doc):
                    raise ValueError(f'{relative} 不是可解析的PDF，或需要密码')
                pages = len(doc)
            job['files'].append({'id': item_id, 'path': relative, 'report_name': stem, 'pages': pages,
                                 'pages_done': 0, 'status': 'pending', 'stage': '等待解析', 'error': None})
        atomic_json(Path(CRITERIA_DIR) / criteria_file, {'schema_version': 'structured-rubric-v1',
                    'title': f'{name} 评分标准', 'background': '', 'language': 'zh', 'base_score': 100, 'criteria': []})
        atomic_json(case / 'case.json', {'kind': 'uploaded_pdf_folder', 'id': job_id, 'name': name,
                    'criteria_files': [criteria_file], 'default_criteria_file': criteria_file, 'created_at': now()})
        save_job(job, f"上传完成：{len(files)}份PDF，共{sum(f['pages'] for f in job['files'])}页")
    except Exception:
        shutil.rmtree(case)
        (Path(CRITERIA_DIR) / criteria_file).unlink(missing_ok=True)
        shutil.rmtree(JOBS_ROOT / job_id, ignore_errors=True)
        raise
    return job


@router.post('')
def upload_folder(name: str = Form(...), paths: str = Form(...), engine: str = Form('hybrid'),
                  files: list[UploadFile] = File(...)):
    try:
        relative_paths = json.loads(paths)
        if not isinstance(relative_paths, list) or not all(isinstance(p, str) for p in relative_paths):
            raise ValueError('无效文件列表')
        job = create_import(name, files, relative_paths, engine)
        start_job(job['id'])
        return {'status': 'ok', 'data': job}
    except FileExistsError:
        raise HTTPException(409, '已存在同名case，请更换名称')
    except (ValueError, fitz.FileDataError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        for upload in files:
            upload.file.close()


@router.get('')
def list_imports():
    recover_imports(include_queued=False)
    jobs = [json.loads(p.read_text()) for p in JOBS_ROOT.glob('*/job.json')]
    return {'status': 'ok', 'data': {'items': sorted(jobs, key=lambda j: j['created_at'], reverse=True)}}


@router.get('/{job_id}')
def get_import(job_id: str):
    try:
        return {'status': 'ok', 'data': read_job(job_id)}
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, '任务不存在')


@router.post('/{job_id}/retry')
def retry_import(job_id: str):
    with _LOCK:
        try:
            job = read_job(job_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, '任务不存在')
        if job['status'] not in TERMINAL or job_id in _ACTIVE:
            raise HTTPException(409, '任务仍在进行中')
        if job['status'] != 'completed':
            job['status'] = 'queued'
            save_job(job, '重试未完成文件；已完成文件不重复解析')
            start_job(job_id)
        return {'status': 'ok', 'data': job}
