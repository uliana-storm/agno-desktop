"""Deterministic one-shot Slack reminders — cron computed server-side."""

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agno.scheduler.manager import ScheduleManager

from server.scheduler_db import get_scheduler_db

MELBOURNE = ZoneInfo("Australia/Melbourne")
DEFAULT_ENDPOINT = "/agents/jarvis/runs"
DEFAULT_TIMEZONE = "Australia/Melbourne"


def _cron_for_datetime(dt: datetime) -> str:
    """Build a 5-field cron for a single fire at dt (minute hour dom month dow)."""
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"


def _normalize_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "reminder"


def _build_payload(
    *,
    message: str,
    session_id: str,
    slack_channel: str,
    thread_ts: str,
) -> dict:
    factory_inner = {
        "message": message,
        "session_id": session_id,
        "slack_channel": slack_channel,
        "thread_ts": thread_ts,
        "is_scheduled": True,
        "disable_schedule_after_run": True,
    }
    return {"factory_input": json.dumps(factory_inner)}


def schedule_reminder_in_minutes(
    minutes: int,
    message: str,
    session_id: str,
    slack_channel: str,
    thread_ts: str,
    name: str = "",
) -> str:
    """Schedule a one-shot Slack reminder N minutes from now (Australia/Melbourne).

    Use this for relative reminders such as "ping me in 5 minutes". Do not compute
    cron yourself — this tool sets the exact fire time and payload fields.

    Args:
        minutes: Minutes from now (must be 1–1440).
        message: Prompt delivered to Jarvis when the reminder fires.
        session_id: Current session_id from Slack location context.
        slack_channel: Slack channel ID from Slack location context.
        thread_ts: Thread timestamp from Slack location context.
        name: Optional schedule name (kebab-case). Auto-generated if empty.

    Returns:
        JSON string with schedule details and human-readable fire time.
    """
    if minutes < 1 or minutes > 1440:
        return json.dumps({"error": "minutes must be between 1 and 1440"})

    if not session_id or not slack_channel or not thread_ts:
        return json.dumps(
            {
                "error": "session_id, slack_channel, and thread_ts are required "
                "(use values from ## Slack location in Additional Context)."
            }
        )

    now = datetime.now(MELBOURNE)
    target = now + timedelta(minutes=minutes)
    cron = _cron_for_datetime(target)

    stamp = now.strftime("%Y%m%d-%H%M")
    schedule_name = _normalize_name(name) if name else f"reminder-in-{minutes}min-{stamp}"

    manager = ScheduleManager(db=get_scheduler_db())
    payload = _build_payload(
        message=message,
        session_id=session_id,
        slack_channel=slack_channel,
        thread_ts=thread_ts,
    )

    try:
        schedule = manager.create(
            name=schedule_name,
            cron=cron,
            endpoint=DEFAULT_ENDPOINT,
            method="POST",
            description=f"One-shot reminder in {minutes} min",
            payload=payload,
            timezone=DEFAULT_TIMEZONE,
            if_exists="update",
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps(
        {
            "status": "created",
            "id": schedule.id,
            "name": schedule_name,
            "cron": cron,
            "timezone": DEFAULT_TIMEZONE,
            "fires_at": target.strftime("%Y-%m-%d %H:%M %Z"),
            "fires_in_minutes": minutes,
            "endpoint": DEFAULT_ENDPOINT,
            "message": message,
            "session_id": session_id,
            "slack_channel": slack_channel,
            "thread_ts": thread_ts,
            "auto_disable_after_run": True,
        }
    )
