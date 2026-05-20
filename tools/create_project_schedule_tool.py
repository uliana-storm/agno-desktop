"""Deterministic recurring project schedules — full payload built server-side."""

import json
import os
import re
from typing import Any

from agno.scheduler.manager import ScheduleManager
from server.scheduler_db import get_scheduler_db

DEFAULT_ENDPOINT = "/agents/tony/runs"
DEFAULT_TIMEZONE = "Australia/Melbourne"

_REQUIRED_KEYS = frozenset(
    {"message", "project", "workfile_path", "slack_channel", "thread_ts"}
)


def _normalize_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "project-schedule"


def _handoff_path(project: str) -> str:
    return os.path.join("projects", project, "handoff.json")


def _load_handoff(project: str) -> dict[str, Any]:
    path = _handoff_path(project)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _resolve_slack_ids(
    project: str,
    slack_channel: str,
    thread_ts: str,
) -> tuple[str, str, list[str]]:
    """Fill missing channel/thread from handoff.json; return warnings."""
    warnings: list[str] = []
    channel = (slack_channel or "").strip()
    thread = (thread_ts or "").strip()

    if not channel or not thread:
        handoff = _load_handoff(project)
        if not channel and handoff.get("slack_channel"):
            channel = str(handoff["slack_channel"]).strip()
            warnings.append("slack_channel taken from handoff.json")
        if not thread and handoff.get("thread_ts"):
            thread = str(handoff["thread_ts"]).strip()
            warnings.append("thread_ts taken from handoff.json")

    return channel, thread, warnings


def build_project_schedule_payload(
    *,
    message: str,
    project: str,
    workfile_path: str,
    slack_channel: str,
    thread_ts: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Canonical payload for Tony project cron runs (flat keys for AgentOS + Slack executor)."""
    payload: dict[str, Any] = {
        "message": message.strip(),
        "project": project.strip(),
        "workfile_path": workfile_path.strip(),
        "slack_channel": slack_channel.strip(),
        "thread_ts": thread_ts.strip(),
        "is_scheduled": True,
    }
    if session_id.strip():
        payload["session_id"] = session_id.strip()
    return payload


def create_project_schedule(
    name: str,
    cron: str,
    message: str,
    project: str,
    slack_channel: str = "",
    thread_ts: str = "",
    session_id: str = "",
    workfile_path: str = "",
    description: str = "",
    timezone: str = DEFAULT_TIMEZONE,
    endpoint: str = DEFAULT_ENDPOINT,
) -> str:
    """Create or update a recurring Tony project schedule with a deterministic payload.

    Use this for ongoing project cron (e.g. daily EOD). Do not use SchedulerTools
    create_schedule — partial payloads replace defaults and drop required fields.

    Args:
        name: Unique kebab-case schedule name (e.g. eod-summary-v2-daily).
        cron: 5-field cron expression (e.g. 0 20 * * * for 8 PM daily).
        message: Prompt for Tony when the schedule fires.
        project: Kebab-case project id (must match projects/{project}/).
        slack_channel: Slack channel ID — from ## Slack location or handoff.json.
        thread_ts: Slack thread ts — required for in-thread delivery.
        session_id: Optional session_id from ## Slack location.
        workfile_path: Path to workfile; defaults to projects/{project}/workfile.md.
        description: Human-readable schedule description.
        timezone: Cron timezone (default Australia/Melbourne).
        endpoint: AgentOS run endpoint (default /agents/tony/runs).

    Returns:
        JSON with schedule metadata and the stored payload for verification.
    """
    project = (project or "").strip()
    if not project:
        return json.dumps({"error": "project is required"})

    schedule_name = _normalize_name(name)
    if not schedule_name:
        return json.dumps({"error": "name is required"})

    cron = (cron or "").strip()
    if not cron:
        return json.dumps({"error": "cron is required"})

    if not (message or "").strip():
        return json.dumps({"error": "message is required"})

    wf = (workfile_path or "").strip() or f"projects/{project}/workfile.md"
    channel, thread, warnings = _resolve_slack_ids(project, slack_channel, thread_ts)

    if not channel or not thread:
        return json.dumps(
            {
                "error": "slack_channel and thread_ts are required "
                "(pass from ## Slack location or ensure projects/{project}/handoff.json has them).",
                "project": project,
                "handoff_path": _handoff_path(project),
            }
        )

    payload = build_project_schedule_payload(
        message=message,
        project=project,
        workfile_path=wf,
        slack_channel=channel,
        thread_ts=thread,
        session_id=session_id,
    )

    missing = _REQUIRED_KEYS - set(payload.keys())
    if missing:
        return json.dumps({"error": f"internal payload missing keys: {sorted(missing)}"})

    manager = ScheduleManager(db=get_scheduler_db())
    try:
        schedule = manager.create(
            name=schedule_name,
            cron=cron,
            endpoint=endpoint or DEFAULT_ENDPOINT,
            method="POST",
            description=description or f"Recurring run for project {project}",
            payload=payload,
            timezone=timezone or DEFAULT_TIMEZONE,
            if_exists="update",
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    result: dict[str, Any] = {
        "status": "created",
        "id": schedule.id,
        "name": schedule.name,
        "cron": schedule.cron_expr,
        "endpoint": schedule.endpoint,
        "timezone": schedule.timezone,
        "enabled": schedule.enabled,
        "description": schedule.description,
        "payload": payload,
    }
    if warnings:
        result["warnings"] = warnings
    return json.dumps(result, indent=2)
