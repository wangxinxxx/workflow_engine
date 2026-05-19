
import os
from pathlib import Path


def _detect_root_dir() -> Path:
    config_file = Path(__file__).resolve()
    package_root = config_file.parents[1]
    workspace_root = config_file.parents[2]
    for candidate in (package_root, workspace_root):
        if (candidate / "templates" / "requirement").exists():
            return candidate
    return package_root


ROOT_DIR = _detect_root_dir()
TEMPLATE_DIR = ROOT_DIR / "templates" / "requirement"
RUNTIME_DIR = ROOT_DIR / ".runtime" / "langgraph"
THREADS_DIR = RUNTIME_DIR / "threads"
CHECKPOINT_DB = RUNTIME_DIR / "checkpoints.sqlite"
STEP_SPECS_PATH = Path(__file__).resolve().parent / "step_specs.json"

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2-codex")


def resolve_requirement_dir(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def ensure_runtime_dirs() -> None:
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
