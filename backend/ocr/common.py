"""Shared OCR file helpers."""
import hashlib
import json
import re
from pathlib import Path

IMAGE_RE = re.compile(r"!\[(.*?)\]\(([^\s]+?)\)", re.DOTALL)


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


