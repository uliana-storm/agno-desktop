"""Build additional_context snippets for agent runs."""

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")

_SCHEDULING_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"\b(?:remind|ping|notify|alert)\b.{0,40}\b(?:in|after)\b.{0,20}\b\d+\s*(?:min(?:ute)?s?|hours?|hrs?)\b"
    r"|"
    r"\b(?:in|after)\s+\d+\s*(?:min(?:ute)?s?|hours?|hrs?)\b.{0,40}\b(?:remind|ping|notify|alert|me)\b"
    r"|"
    r"\b(?:schedule|set\s+(?:a\s+)?reminder|create\s+(?:a\s+)?schedule)\b"
    r"|"
    r"\b(?:every|daily|weekly|weekday|weekdays|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.{0,30}\b(?:at|@)\b"
    r"|"
    r"\b(?:at|@)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b.{0,30}\b(?:remind|ping|every|daily|weekly)\b"
    r")"
)


def melbourne_now() -> datetime:
    return datetime.now(MELBOURNE_TZ)


def melbourne_datetime_context() -> str:
    now = melbourne_now()
    return (
        f"\n\n## Current time (Australia/Melbourne)\n"
        f"- now: {now.strftime('%Y-%m-%d %H:%M %A %Z')}\n"
        f"- timezone: Australia/Melbourne\n"
    )


def looks_like_scheduling_request(text: str) -> bool:
    if not text:
        return False
    return bool(_SCHEDULING_RE.search(text))


def scheduling_datetime_context(user_prompt: str = "") -> str:
    """Inject clock + tool guidance when the user is asking to schedule something."""
    lines = [
        melbourne_datetime_context().strip(),
        "",
        "## Scheduling context",
        "- detected_scheduling_intent: yes",
        f"- user_request: {user_prompt.strip()[:200]}" if user_prompt else "- user_request: (see message)",
        "",
        "*Relative reminders* (e.g. 'in 5 minutes', 'ping me in 1 hour'):",
        "- MUST call `schedule_reminder_in_minutes` — never compute cron manually.",
        "- Pass session_id, slack_channel, thread_ts exactly from ## Slack location above.",
        "",
        "*Recurring schedules* (daily, weekly, weekdays at 5pm):",
        "- Use `create_schedule` with cron computed from the current time above.",
        "- Wrap run payload in factory_input JSON string per your prompt.",
    ]
    return "\n" + "\n".join(lines) + "\n"


def slack_location_context(
    channel: str,
    thread_ts: str,
    session_id: Optional[str] = None,
) -> str:
    """Dynamic Slack IDs only — tool routing lives in the agent prompt."""
    if not channel:
        return ""
    session_line = f"- session_id: {session_id}\n" if session_id else ""
    return (
        f"\n\n## Slack location\n"
        f"- slack_channel: {channel}\n"
        f"- thread_ts: {thread_ts}\n"
        f"{session_line}"
        f"- in_thread: yes\n"
    )


def jarvis_slack_instructions() -> str:
    """Minimal live-run delivery note for Jarvis."""
    return (
        "\n\nYour reply streams to the thread automatically. "
        "Do not send it again via any tool — that duplicates the stream."
    )


def tony_slack_instructions() -> str:
    """Live-run delivery note for Tony."""
    return (
        "\n\n## Slack delivery\n"
        "Your reply streams automatically. Do not resend it via any tool.\n"
        "Use upload_deliverable(scope, path) only when attaching files to the thread.\n"
    )