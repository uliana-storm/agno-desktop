"""
Handoff tool — allows Jarvis to transfer work to Tony.

This tool packages a project brief and queues it for Tony.
Jarvis only. Tony never has this tool.
"""

import json
from datetime import datetime

from bot.router import similar_projects
from server.paths import PROJECTS_DIR, similarity_sentinel_path


def handoff_to_tony(
    goal: str,
    success_markers: list[str],
    scope: str,
    dept: str,
    project_name: str,
    constraints: list[str] | None = None,
    knowledge_refs: list[str] | None = None,
    task: str = "",
    slack_channel: str = "",
    thread_ts: str = "",
) -> str:
    """
    Call this when the user has explicitly confirmed the project brief
    and work should begin. Packages the brief and queues it for Tony.

    IMPORTANT: Only call this after the user has said yes, proceed,
    go ahead, or equivalent. Never call speculatively.

    Always pass slack_channel and thread_ts from ## Slack location.

    Returns:
        "HANDOFF_READY:{project_name}" — bot dispatches Tony, or
        "SIMILAR_PROJECTS" — bot reads sentinel and asks user to choose.
        Do not interpret either return value yourself.
    """
    constraints    = constraints or []
    knowledge_refs = knowledge_refs or []

    brief_params = {
        "goal":             goal,
        "success_markers":  success_markers,
        "constraints":      constraints,
        "scope":            scope,
        "dept":             dept,
        "project_name":     project_name,
        "knowledge_refs":   knowledge_refs,
        "needs_resolution": len(knowledge_refs) == 0,
        "created":          datetime.now().isoformat(),
    }
    if task:
        brief_params["task"] = task
    if slack_channel:
        brief_params["slack_channel"] = slack_channel
    if thread_ts:
        brief_params["thread_ts"] = thread_ts

    similar = similar_projects(project_name, goal) if thread_ts else []
    if similar:
        sentinel = {
            "proposed_name": project_name,
            "matches":       similar,
            "brief_params":  brief_params,
        }
        path = similarity_sentinel_path(thread_ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(sentinel, f, indent=2)
        return "SIMILAR_PROJECTS"

    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = project_dir / "handoff.json"
    with open(handoff_path, "w") as f:
        json.dump(brief_params, f, indent=2)

    return f"HANDOFF_READY:{project_name}"
