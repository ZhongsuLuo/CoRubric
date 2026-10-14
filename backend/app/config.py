import os
from pathlib import Path

from backend.libs.config import GRADING_MODEL_CONFIG

# backend.libs.config loads backend/.env before either configuration is evaluated.

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_DATA_DIR = PROJECT_ROOT / "backend" / "data" / "api"
DEFAULT_CRITERIA_DIR = PROJECT_ROOT / "backend" / "data" / "criteria"
DEFAULT_CRITERIA_BASELINE_DIR = PROJECT_ROOT / "backend" / "data" / "criteria_baselines"
DEFAULT_CASE_BASELINE_DIR = PROJECT_ROOT / "backend" / "data" / "case_baselines"
DEFAULT_CASE_ROOT = PROJECT_ROOT / "backend" / "data" / "case"

API_DATA_DIR = Path(os.getenv("GRADING_DATA_DIR", str(DEFAULT_API_DATA_DIR)))
CRITERIA_DIR = Path(os.getenv("GRADING_CRITERIA_DIR", str(DEFAULT_CRITERIA_DIR)))
CRITERIA_BASELINE_DIR = Path(
    os.getenv("GRADING_CRITERIA_BASELINE_DIR", str(DEFAULT_CRITERIA_BASELINE_DIR))
)
CASE_BASELINE_DIR = Path(
    os.getenv("GRADING_CASE_BASELINE_DIR", str(DEFAULT_CASE_BASELINE_DIR))
)
RESULTS_DIR = Path(os.getenv("GRADING_RESULTS_DIR", str(API_DATA_DIR / "results")))
CASE_ROOT = Path(os.getenv("GRADING_CASE_ROOT", str(DEFAULT_CASE_ROOT)))

ATOMIC_ABSORPTION_CRITERIA_FILE = (
    "“原子吸收光谱法测定饮用水中的镁含量”实验评分标准_criterion_categories_v2.json"
)
CASE_ONE_CRITERIA_FILE = "Case 1 评分标准.json"
CASE_CRITERIA_FILES = {
    "case 1": CASE_ONE_CRITERIA_FILE,
    "case 2": ATOMIC_ABSORPTION_CRITERIA_FILE,
}
VISIBLE_CASE_IDS = tuple(CASE_CRITERIA_FILES)
EXPERIMENT_RUBRIC_BASELINES = CASE_CRITERIA_FILES

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

GRADING_TEACHER_ID = os.getenv("GRADING_TEACHER_ID", "default-teacher")
# Provenance uses the actual shared runtime model configuration.
GRADING_MODEL_ID = GRADING_MODEL_CONFIG.model


def _non_negative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


# Set to 0 to allow rubric-only model pre-grades before the first teacher grade.
# Raising it restores a teacher-evidence gate per criterion.
AUTO_GRADE_MIN_TEACHER_SAMPLES = _non_negative_int_env("AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
# Independent criterion inference runs outside the case lock. Keep concurrency
# conservative to reduce first-report latency without flooding model providers.
AUTO_GRADE_MAX_WORKERS = max(1, _non_negative_int_env("AUTO_GRADE_MAX_WORKERS", 3))
READINESS_POLICY_VERSION = os.getenv("READINESS_POLICY_VERSION", "v2")

# Criterion-level model/teacher consistency automation. Legacy disables the
# feature, shadow records evidence without changing scheduling, and adaptive
# enables per-criterion full backfill once the smoothed agreement is reliable.
CONSISTENCY_AUTOMATION_MODE = os.getenv("CONSISTENCY_AUTOMATION_MODE", "adaptive").strip().lower()
if CONSISTENCY_AUTOMATION_MODE not in {"legacy", "shadow", "adaptive"}:
    CONSISTENCY_AUTOMATION_MODE = "adaptive"
CONSISTENCY_RELIABLE_THRESHOLD = _float_env("CONSISTENCY_RELIABLE_THRESHOLD", 0.80)
CONSISTENCY_PRIOR_ALPHA = _float_env("CONSISTENCY_PRIOR_ALPHA", 1.0)
CONSISTENCY_PRIOR_BETA = _float_env("CONSISTENCY_PRIOR_BETA", 1.0)
CONSISTENCY_CALIBRATING_REVIEW_RATE = _float_env("CONSISTENCY_CALIBRATING_REVIEW_RATE", 0.50)
CONSISTENCY_RELIABLE_REVIEW_RATE = _float_env("CONSISTENCY_RELIABLE_REVIEW_RATE", 0.10)
CONSISTENCY_UNRELIABLE_REVIEW_RATE = _float_env("CONSISTENCY_UNRELIABLE_REVIEW_RATE", 1.0)
CONSISTENCY_SCORER_REVISION = os.getenv("CONSISTENCY_SCORER_REVISION", "v1")
CONSISTENCY_REVIEW_POLICY_VERSION = os.getenv("CONSISTENCY_REVIEW_POLICY_VERSION", "v1")
