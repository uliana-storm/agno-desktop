"""
Router module — pre-routing logic for the Slack bot.

Handles:
- Keyword matching against the project registry
- Active project lookup by thread timestamp (file-backed persistence)
- Master routing decision logic
- Thread state registration

No model inference here — pure string matching and state lookup.
"""

import fcntl
import json
import os
from pathlib import Path
from typing import Optional

from server.paths import ACTIVE_THREADS_PATH


# Thread state persistence
THREAD_STATE_PATH = ACTIVE_THREADS_PATH


# In-memory store with refresh tracking
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


def _save_thread_state(state: dict):
    """Save active thread-to-project mappings to file with locking."""
    THREAD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(THREAD_STATE_PATH, "w") as f:
        # Acquire exclusive lock for atomic write
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# Initialize cache at import time
_active_threads_cache = _load_thread_state()


def keyword_match(message_text: str, index_path: str = "projects/index.md") -> list[dict]:
    """
    Loads projects/index.md, parses project entries, returns list of
    matching projects based on keyword overlap.
    Pure string matching — no model inference.
    Returns [] if no matches found.
    """
    if not os.path.exists(index_path):
        return []

    with open(index_path) as f:
        content = f.read()

    projects = []
    current = {}

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("## "):
            if current:
                projects.append(current)
            current = {"name": line[3:].strip(), "keywords": [], "status": "", "summary": ""}
        elif line.startswith("keywords:"):
            keywords_raw = line.replace("keywords:", "").strip()
            current["keywords"] = [k.strip() for k in keywords_raw.split(",")]
        elif line.startswith("status:"):
            current["status"] = line.replace("status:", "").strip()
        elif line.startswith("summary:"):
            current["summary"] = line.replace("summary:", "").strip()
        elif line.startswith("workfile:"):
            current["workfile"] = line.replace("workfile:", "").strip()

    if current:
        projects.append(current)

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
        # Project registered but workfile not yet created — Tony is on first run
        handoff_path = f"projects/{project_name}/handoff.json"
        if os.path.exists(handoff_path):
            with open(handoff_path) as f:
                return {"project_name": project_name, "handoff": json.load(f)}
        return None

    return {"project_name": project_name, "workfile_path": workfile_path}


def register_thread(thread_ts: str, project_name: str):
    """
    Called by slack_bot.py after a successful handoff to Tony.
    Persists the thread → project mapping.
    """
    global _active_threads_cache
    _active_threads_cache[thread_ts] = project_name
    _save_thread_state(_active_threads_cache)


def route(message_text: str, thread_ts: str) -> dict:
    """
    Master routing function.

    Returns:
    {
        "agent": "jarvis" | "tony",
        "mode": "continue" | "project_select" | "casual",
        "project": dict | None,
        "matches": list[dict]
    }
    """
    # 1. Active project thread — highest priority, goes straight to Tony
    project = get_active_project(thread_ts)
    if project:
        return {
            "agent": "tony",
            "mode": "continue",
            "project": project,
            "matches": []
        }

    # 2. Keyword match — Jarvis surfaces options
    matches = keyword_match(message_text, "projects/index.md")
    if matches:
        return {
            "agent": "jarvis",
            "mode": "project_select",
            "project": None,
            "matches": matches
        }

    # 3. Everything else — Jarvis handles
    return {
        "agent": "jarvis",
        "mode": "casual",
        "project": None,
        "matches": []
    }
