"""Recurring project schedules for Tony — wraps AgentOS ScheduleManager."""

import json
import re

from agno.scheduler.manager import ScheduleManager

from server.scheduler_db import get_scheduler_db

TONY_ENDPOINT = "/agents/tony/runs"
DEFAULT_TZ = "Australia/Melbourne"


def _normalize_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "project-schedule"


def _build_payload(
    *,
    message: str,
    session_id: str,
    slack_channel: str,
    thread_ts: str,
    project: str,
    workfile_path: str,
) -> dict:
    factory_inner = {
        "message": message,
        "session_id": session_id,
        "slack_channel": slack_channel,
        "thread_ts": thread_ts,
        "project": project,
        "workfile_path": workfile_path,
        "is_scheduled": True,
    }
    return {"factory_input": json.dumps(factory_inner)}


def create_project_schedule(
    name: str,
    cron: str,
    message: str,
    project: str,
    session_id: str,
    slack_channel: str,
    thread_ts: str,
    description: str = "",
    workfile_path: str = "",
    timezone: str = DEFAULT_TZ,
) -> str:
    """Create a recurring AgentOS schedule that runs Tony on a cron expression.

    Use for ongoing project jobs: daily EOD summaries, weekly reports, etc.
    Do not use for one-shot reminders — use schedule_reminder_in_minutes instead.

    Args:
        name:          Kebab-case schedule name, unique per project
                       (e.g. "dev-eod-summary-daily").
        cron:          5-field cron expression in the given timezone
                       (e.g. "0 6 * * *" for 6 AM daily).
        message:       Prompt Tony receives when the schedule fires.
        project:       Kebab-case project id matching the workfile directory.
        session_id:    Current session_id from ## Slack location.
        slack_channel: Slack channel ID from ## Slack location.
        thread_ts:     Thread timestamp from ## Slack location.
        description:   Optional human-readable description of the schedule.
        workfile_path: Override workfile path (defaults to projects/{project}/workfile.md).
        timezone:      Cron timezone. Defaults to Australia/Melbourne.

    Returns:
        JSON string with schedule id, name, cron, and next fire time.
    """
    if not all([name, cron, message, project, session_id, slack_channel, thread_ts]):
        return "name, cron, message, project, session_id, slack_channel, and thread_ts are all required."

    cron_parts = cron.strip().split()
    if len(cron_parts) != 5:
        return f"Invalid cron expression '{cron}' — must be 5 fields (minute hour dom month dow)."

    schedule_name = _normalize_name(name)
    resolved_wf = workfile_path or f"projects/{project}/workfile.md"
    resolved_desc = description or f"Recurring schedule for project '{project}'"

    payload = _build_payload(
        message=message,
        session_id=session_id,
        slack_channel=slack_channel,
        thread_ts=thread_ts,
        project=project,
        workfile_path=resolved_wf,
    )

    manager = ScheduleManager(db=get_scheduler_db())

    try:
        schedule = manager.create(
            name=schedule_name,
            cron=cron,
            endpoint=TONY_ENDPOINT,
            method="POST",
            description=resolved_desc,
            payload=payload,
            timezone=timezone,
            if_exists="update",
        )
    except Exception as exc:
        return f"Failed to create schedule: {exc}"

    return json.dumps(
        {
            "id": schedule.id,
            "name": schedule.name,
            "cron": cron,
            "timezone": timezone,
            "project": project,
            "endpoint": TONY_ENDPOINT,
        },
        indent=2,
    )
