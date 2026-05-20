"""Post Block Kit EOD reports to Slack."""

import json

from bot.slack_notify import post_blocks_to_slack
from tools.slack_report_blocks import build_eod_blocks


def post_eod_report(
    channel: str,
    date: str,
    done_items: list[str],
    unanswered: list[dict],
    in_progress: list[str],
    trend: str = "",
    thread_ts: str = "",
) -> str:
    """Post a formatted EOD report using Slack Block Kit.

    Args:
        channel: Slack channel ID to post the report to.
        date: Report date label (e.g. 2026-05-20).
        done_items: Completed items for the done section.
        unanswered: List of dicts with user, message, hours, link keys.
        in_progress: Items still in progress.
        trend: Optional trend note (omit if empty).
        thread_ts: Optional thread to reply in.

    Returns:
        JSON success or error string.
    """
    blocks = build_eod_blocks(
        date=date,
        done_items=done_items or [],
        unanswered=unanswered or [],
        in_progress=in_progress or [],
        trend=trend or "",
    )
    fallback = f"Daily Report — {date}"
    try:
        post_blocks_to_slack(channel, blocks, fallback, thread_ts or None)
        return json.dumps({"status": "ok", "channel": channel, "blocks_count": len(blocks)})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
