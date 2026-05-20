"""Canonical directory paths for the agno-desktop project."""

import shutil
from pathlib import Path

MEMORY_DIR = Path("memory")
OUTPUT_DIR = Path("output")
PROJECTS_DIR = Path("projects")
KNOWLEDGE_DIR = Path("knowledge")

REPORTS_DIR = OUTPUT_DIR / "reports"
PROJECT_SERVER_DIR = OUTPUT_DIR / "project_server"

GUIDANCE_DIR = KNOWLEDGE_DIR / "core"

DELIVERABLE_SCAN_ROOTS = [REPORTS_DIR, PROJECT_SERVER_DIR, PROJECTS_DIR]

JARVIS_MEMORY_DB = MEMORY_DIR / "jarvis_memory.db"
TONY_MEMORY_DB = MEMORY_DIR / "tony_memory.db"
ACTIVE_THREADS_PATH = MEMORY_DIR / "active_threads.json"

# Legacy locations (pre-migration)
LEGACY_DB_DIR = OUTPUT_DIR / "db"
LEGACY_JARVIS_MEMORY_DB = LEGACY_DB_DIR / "jarvis_memory.db"
LEGACY_TONY_MEMORY_DB = LEGACY_DB_DIR / "tony_memory.db"
LEGACY_ACTIVE_THREADS_PATH = LEGACY_DB_DIR / "active_threads.json"

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
    new_files = [p for p in (current - before) if _is_deliverable(p)]
    return new_files


def migrate_legacy_memory() -> None:
    """Move memory DBs and router state from output/db/ to memory/."""
    ensure_dirs()
    moves = [
        (LEGACY_JARVIS_MEMORY_DB, JARVIS_MEMORY_DB),
        (LEGACY_TONY_MEMORY_DB, TONY_MEMORY_DB),
        (LEGACY_ACTIVE_THREADS_PATH, ACTIVE_THREADS_PATH),
    ]
    for src, dest in moves:
        if src.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            print(f"[paths] Migrated {src} -> {dest}", flush=True)


def migrate_legacy_paths() -> None:
    """One-time cleanup for nested output/ and misplaced workfiles."""
    ensure_dirs()
    migrate_legacy_memory()

    nested_reports = OUTPUT_DIR / "output" / "reports"
    if nested_reports.exists():
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        for src in nested_reports.iterdir():
            if src.is_file():
                dest = REPORTS_DIR / src.name
                if not dest.exists():
                    shutil.move(str(src), str(dest))
                    print(f"[paths] Migrated {src} -> {dest}", flush=True)

    legacy_project_workfiles = OUTPUT_DIR / "projects"
    if legacy_project_workfiles.exists():
        for project_dir in legacy_project_workfiles.iterdir():
            if not project_dir.is_dir():
                continue
            workfile = project_dir / "workfile.md"
            if workfile.exists():
                dest_dir = PROJECTS_DIR / project_dir.name
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / "workfile.md"
                if not dest.exists():
                    shutil.move(str(workfile), str(dest))
                    print(f"[paths] Migrated {workfile} -> {dest}", flush=True)

    nested_output = OUTPUT_DIR / "output"
    if nested_output.exists() and not any(nested_output.rglob("*")):
        nested_output.rmdir()
        print(f"[paths] Removed empty {nested_output}", flush=True)

    legacy_project_server = OUTPUT_DIR / "server"
    if legacy_project_server.exists():
        PROJECT_SERVER_DIR.mkdir(parents=True, exist_ok=True)
        for src in legacy_project_server.iterdir():
            dest = PROJECT_SERVER_DIR / src.name
            if dest.exists():
                continue
            shutil.move(str(src), str(dest))
            print(f"[paths] Migrated {src} -> {dest}", flush=True)
        if not any(legacy_project_server.iterdir()):
            legacy_project_server.rmdir()
            print(f"[paths] Removed empty {legacy_project_server}", flush=True)

    if LEGACY_DB_DIR.exists() and not any(LEGACY_DB_DIR.iterdir()):
        LEGACY_DB_DIR.rmdir()
        print(f"[paths] Removed empty {LEGACY_DB_DIR}", flush=True)
