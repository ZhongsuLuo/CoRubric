"""Persisted metadata for uploaded cases; legacy demo cases keep their configuration."""
import json
import os
import tempfile
from pathlib import Path

from .config import CASE_ROOT
from .criteria_lock import criteria_file_lock, canonical_criteria_file_name


def safe_name(value: str) -> str:
    if (not value or value != value.strip() or value in {'.', '..'} or value.startswith('.')
            or len(value.encode('utf-8')) > 200 or any(c in value for c in '/\\:')
            or any(ord(c) < 32 for c in value)):
        raise ValueError('名称不能为空，不能包含路径分隔符，长度不能超过200字节')
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.writing-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def metadata(case_id: str, root=None) -> dict | None:
    path = Path(root if root is not None else CASE_ROOT) / safe_name(case_id) / 'case.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    return data if data.get('kind') == 'uploaded_pdf_folder' else None


def imported_cases(root=None) -> list[str]:
    base = Path(root if root is not None else CASE_ROOT)
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink()
                  and not p.name.startswith('.') and metadata(p.name, base))


def rubric_target(case_id: str, requested: str) -> str:
    data = metadata(case_id)
    if not data:
        raise ValueError('该 case 不是上传的文件夹')
    safe_name(requested)
    name = canonical_criteria_file_name(requested)
    if name in data.get('criteria_files', []):
        return name
    # Case-specific namespace prevents a newly created rubric overwriting another case.
    return f"{data['id']}_{name}"


def bind_rubric(case_id: str, file_name: str) -> None:
    with criteria_file_lock(CASE_ROOT, case_id):
        data = metadata(case_id)
        if not data:
            raise ValueError('Case metadata not found')
        data['criteria_files'] = list(dict.fromkeys([*data.get('criteria_files', []), file_name]))
        data['default_criteria_file'] = file_name
        atomic_json(Path(CASE_ROOT) / case_id / 'case.json', data)
