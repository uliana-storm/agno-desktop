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
    now = melbourne_now()
    lines = [
        melbourne_datetime_context().strip(),
        "",
        "## Scheduling context",
        f"- detected_scheduling_intent: yes",
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
    """Minimal live-run delivery note — location is in additional_context only."""
    return (
        "\n\nYour reply streams to the thread in ## Slack location automatically. "
        "Do not call post_to_slack or send_message for a normal answer — that duplicates the stream."
    )


def task_context(task: str, message: str = "") -> str:
    if not task:
        return ""
    lines = [f"\n\n## Task\n- task: {task}"]
    if message:
        lines.append(f"- message: {message}")
    return "\n".join(lines)


def eod_run_instructions(task: str) -> str:
    if task == "eod_report_init":
        return (
            "\n\n## EOD init instructions\n"
            "1. Call list_channels(include_private=True) and keep only channels the bot is in.\n"
            "2. Create projects/slack_eod_report/workfile.md with meta, config JSON block, "
            "report_history, and trends sections per your prompt.\n"
            "3. Set deliver_to.channel_id to the slack_channel from Task Context.\n"
            "4. create_schedule cron 0 17 * * 1-5 endpoint /agents/tony/runs with factory_input "
            "including task=eod_report_run, project=slack_eod_report, session_id=tony-slack-eod, "
            "slack_channel, thread_ts.\n"
            "5. send_message_thread confirmation to the user.\n"
            "6. Do NOT stream the full report — post via send_message or post_eod_report when reporting."
        )
    if task == "eod_report_run":
        return (
            "\n\n## EOD run instructions\n"
            "1. Read projects/slack_eod_report/workfile.md config JSON first.\n"
            "2. For each channel in config.channels: get_channel_history, then get_thread for "
            "messages with reply_count > 0.\n"
            "3. Analyse per analysis_scope; append ## report_history line; append ## trends if 3-day pattern.\n"
            "4. Call post_eod_report for deliver_to.channel_id (NOT post_to_slack for the full report).\n"
            "5. Reply with a one-line confirmation only (streaming handles short ack)."
        )
    if task == "eod_report_adjust":
        return (
            "\n\n## EOD adjust instructions\n"
            "Update ## config JSON in projects/slack_eod_report/workfile.md per user request. "
            "Reschedule only if they explicitly change timing."
        )
    return ""
