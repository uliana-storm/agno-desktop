"""Canonical directory paths for the agno-desktop project."""

from pathlib import Path

# Get project root (directory containing this file's parent)
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()

MEMORY_DIR = _PROJECT_ROOT / "memory"
OUTPUT_DIR = _PROJECT_ROOT / "output"
PROJECTS_DIR = _PROJECT_ROOT / "projects"
KNOWLEDGE_DIR = _PROJECT_ROOT / "knowledge"

REPORTS_DIR = OUTPUT_DIR / "reports"
PROJECT_SERVER_DIR = OUTPUT_DIR / "project_server"

GUIDANCE_DIR = KNOWLEDGE_DIR / "core"

DELIVERABLE_SCAN_ROOTS = [REPORTS_DIR, PROJECT_SERVER_DIR, PROJECTS_DIR]

JARVIS_MEMORY_DB = MEMORY_DIR / "jarvis_memory.db"
TONY_MEMORY_DB = MEMORY_DIR / "tony_memory.db"
ACTIVE_THREADS_PATH = MEMORY_DIR / "active_threads.json"

_EXCLUDED_SUFFIXES = {".db"}
_EXCLUDED_NAMES = {"active_threads.json"}


def ensure_dirs() -> None:
    """Create standard directories if missing."""
    for path in (MEMORY_DIR, OUTPUT_DIR, REPORTS_DIR, PROJECT_SERVER_DIR, PROJECTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _is_deliverable(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in _EXCLUDED_SUFFIXES:
        return False
    if path.name in _EXCLUDED_NAMES:
        return False
    return True


def snapshot_deliverable_files() -> set[Path]:
    """Return all deliverable files under scan roots."""
    files: set[Path] = set()
    for root in DELIVERABLE_SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if _is_deliverable(path):
                files.add(path.resolve())
    return files


def find_new_deliverable_files(before: set[Path]) -> list[Path]:
    """Return deliverable files created since before snapshot."""
    current = snapshot_deliverable_files()
    return [p for p in (current - before) if _is_deliverable(p)]
