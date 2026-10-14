import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def disable_background_revision_threads(monkeypatch):
    """Keep revision workflows deterministic; dispatch is tested with a spy."""
    from backend.app.grading import revisions

    monkeypatch.setattr(
        revisions,
        "ensure_criterion_revision_worker",
        lambda _case_id, _criteria_file_name, _criterion_id: False,
    )
