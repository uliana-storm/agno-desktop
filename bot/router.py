"""
Router module — pre-routing logic for the Slack bot.

Handles:
- Keyword matching against the project registry (cached, mtime-refreshed)
- Active project lookup by thread timestamp (file-backed persistence)
- Escape hatch: explicit Jarvis signals bypass active Tony thread
- Project close: explicit close intent deregisters the thread
- Master routing decision logic
- Thread state registration / deregistration

No model inference here — pure string matching and state lookup.
"""

import fcntl
import json
import os
import re
from pathlib import Path
from typing import Optional

from server.paths import ACTIVE_THREADS_PATH

THREAD_STATE_PATH = ACTIVE_THREADS_PATH

# ---------------------------------------------------------------------------
# Escape and close patterns
# ---------------------------------------------------------------------------

# These phrases route to Jarvis even when a Tony project thread is active.
# The thread stays registered — project is bypassed for this message only.
_JARVIS_ESCAPE_RE = re.compile(
    r"(?i)\b(?:"
    r"hey\s+jarvis"
    r"|@jarvis"
    r"|jarvis[,!?]"
    r"|back\s+to\s+jarvis"
    r"|switch\s+to\s+jarvis"
    r"|go\s+to\s+jarvis"
    r"|ask\s+jarvis"
    r"|new\s+topic"
    r"|different\s+question"
    r")\b"
)

# These phrases deregister the thread entirely and route to Jarvis.
_PROJECT_CLOSE_RE = re.compile(
    r"(?i)\b(?:"
    r"close\s+project"
    r"|end\s+project"
    r"|done\s+with\s+(?:the\s+)?project"
    r"|project\s+(?:is\s+)?(?:done|complete|finished|closed)"
    r"|finish\s+project"
    r"|wrap\s+(?:up\s+)?project"
    r")\b"
)


# ---------------------------------------------------------------------------
# Thread state persistence
# ---------------------------------------------------------------------------

_active_threads_cache: dict = {}
_last_load_time: float = 0


def _load_thread_state() -> dict:
    """Load active thread-to-project mappings from file."""
    global _last_load_time
    if THREAD_STATE_PATH.exists():
        with open(THREAD_STATE_PATH) as f:
            _last_load_time = os.path.getmtime(THREAD_STATE_PATH)
            return json.load(f)
    return {}


def _get_active_threads() -> dict:
    """Get thread state, refreshing from disk if file has changed."""
    global _active_threads_cache
    if THREAD_STATE_PATH.exists():
        mtime = os.path.getmtime(THREAD_STATE_PATH)
        if mtime > _last_load_time:
            _active_threads_cache = _load_thread_state()
    return _active_threads_cache


def _save_thread_state(state: dict) -> None:
    """Save active thread-to-project mappings to file with locking."""
    THREAD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(THREAD_STATE_PATH, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# Initialize cache at import time
_active_threads_cache = _load_thread_state()


def register_thread(thread_ts: str, project_name: str) -> None:
    """
    Called after a successful handoff to Tony.
    Persists the thread → project mapping.
    """
    global _active_threads_cache
    _active_threads_cache[thread_ts] = project_name
    _save_thread_state(_active_threads_cache)


def unregister_thread(thread_ts: str) -> None:
    """
    Called when a project is explicitly closed.
    Removes the thread → project mapping so Jarvis handles the thread again.
    """
    global _active_threads_cache
    if thread_ts in _active_threads_cache:
        del _active_threads_cache[thread_ts]
        _save_thread_state(_active_threads_cache)


# ---------------------------------------------------------------------------
# Project index cache
# ---------------------------------------------------------------------------

_index_cache: list[dict] = []
_index_mtime: float = 0


def _load_index(index_path: str) -> list[dict]:
    """Parse projects/index.md into a list of project dicts."""
    global _index_cache, _index_mtime

    if not os.path.exists(index_path):
        return []

    mtime = os.path.getmtime(index_path)
    if mtime <= _index_mtime and _index_cache:
        return _index_cache

    with open(index_path) as f:
        content = f.read()

    projects = []
    current: dict = {}

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("## "):
            if current:
                projects.append(current)
            current = {
                "name":     line[3:].strip(),
                "keywords": [],
                "status":   "",
                "summary":  "",
            }
        elif line.startswith("keywords:"):
            raw = line.replace("keywords:", "").strip()
            current["keywords"] = [k.strip() for k in raw.split(",") if k.strip()]
        elif line.startswith("status:"):
            current["status"] = line.replace("status:", "").strip()
        elif line.startswith("summary:"):
            current["summary"] = line.replace("summary:", "").strip()
        elif line.startswith("workfile:"):
            current["workfile"] = line.replace("workfile:", "").strip()

    if current:
        projects.append(current)

    _index_cache = projects
    _index_mtime = mtime
    return _index_cache


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def keyword_match(
    message_text: str,
    index_path: str = "projects/index.md",
) -> list[dict]:
    """
    Returns list of projects whose keywords appear in message_text.
    Uses cached index — re-reads only when index.md changes on disk.
    Pure string matching, no model inference.
    """
    projects = _load_index(index_path)
    message_lower = message_text.lower()
    matches = []
    for project in projects:
        for keyword in project.get("keywords", []):
            if keyword.lower() in message_lower:
                matches.append(project)
                break
    return matches


def get_active_project(thread_ts: str) -> dict | None:
    """
    Returns project metadata if thread_ts is registered as an active
    Tony project. None otherwise.
    """
    project_name = _get_active_threads().get(thread_ts)
    if not project_name:
        return None

    workfile_path = f"projects/{project_name}/workfile.md"
    if not os.path.exists(workfile_path):
        handoff_path = f"projects/{project_name}/handoff.json"
        if os.path.exists(handoff_path):
            with open(handoff_path) as f:
                return {"project_name": project_name, "handoff": json.load(f)}
        return None

    return {"project_name": project_name, "workfile_path": workfile_path}


# ---------------------------------------------------------------------------
# Master router
# ---------------------------------------------------------------------------

def route(message_text: str, thread_ts: str) -> dict:
    """
    Master routing function.

    Priority order:
      1. Project close signal  → Jarvis, deregister thread
      2. Jarvis escape signal  → Jarvis, keep thread registered
      3. Active project thread → Tony
      4. Keyword match         → Jarvis (project_select)
      5. Default               → Jarvis (casual)

    Returns:
        {
            "agent":   "jarvis" | "tony",
            "mode":    "continue" | "project_select" | "casual"
                       | "escape" | "project_closed",
            "project": dict | None,
            "matches": list[dict],
        }
    """
    # 1. Explicit project close — deregister and hand back to Jarvis
    if _PROJECT_CLOSE_RE.search(message_text):
        project = get_active_project(thread_ts)
        project_name = project.get("project_name", "") if project else ""
        unregister_thread(thread_ts)
        return {
            "agent":   "jarvis",
            "mode":    "project_closed",
            "project": project,
            "matches": [],
            "closed_project": project_name,
        }

    # 2. Explicit Jarvis escape — bypass Tony for this message only
    if _JARVIS_ESCAPE_RE.search(message_text):
        return {
            "agent":   "jarvis",
            "mode":    "escape",
            "project": None,
            "matches": [],
        }

    # 3. Active project thread — Tony continues
    project = get_active_project(thread_ts)
    if project:
        return {
            "agent":   "tony",
            "mode":    "continue",
            "project": project,
            "matches": [],
        }

    # 4. Keyword match — Jarvis surfaces options
    matches = keyword_match(message_text, "projects/index.md")
    if matches:
        return {
            "agent":   "jarvis",
            "mode":    "project_select",
            "project": None,
            "matches": matches,
        }

    # 5. Default — Jarvis handles
    return {
        "agent":   "jarvis",
        "mode":    "casual",
        "project": None,
        "matches": [],
    }