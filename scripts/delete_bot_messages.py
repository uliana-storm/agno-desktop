"""
delete_bot_messages.py

Delete bot messages from a Slack channel by type.
Run from project root: .venv/bin/python scripts/delete_bot_messages.py

Types observed in logs:
  tool_calls    — 🔧 tool_name — {args} posts
  errors        — ❌ Error: ... posts
  progress      — inline agent status/reasoning text (edited messages)
  upload_ack    — "EOD Summary — ... Full HTML report attached below."
  html_content  — raw HTML file content posted as a message
  timing        — _(Ns)_ timing posts
  all_bot       — every bot message
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Config — edit before running
# ---------------------------------------------------------------------------

CHANNEL = "D0B566S8A0N"   # channel to clean up
LIMIT   = 500             # how many recent messages to fetch
DRY_RUN = True            # set False to actually delete

# Which types to delete — comment out what you want to keep
DELETE_TYPES = [
    "tool_calls",
    "errors",
    "progress",
    "upload_ack",
    "html_content",
    "timing",
    # "all_bot",    # uncomment to delete everything the bot posted
]

# ---------------------------------------------------------------------------
# Message type matchers
# ---------------------------------------------------------------------------

TYPE_PATTERNS: dict[str, list[str]] = {
    "tool_calls": [
        # current format: `tool_name` — `{args}`
        "`get_channel_history`",
        "`get_channel_info`",
        "`get_thread`",
        "`read_file`",
        "`save_file`",
        "`append_file`",
        "`list_files`",
        "`search_files`",
        "`generate_html_report`",
        "`generate_html_from_markdown`",
        "`upload_deliverable`",
        "`list_channels`",
        "`create_project_schedule`",
        "`delete_schedule`",
        "`list_schedules`",
        "`get_channel_history` —",
        # legacy format with emoji (keep for older messages)
        "🔧 `",
    ],
    "errors": [
        # current format: no emoji
        "Error: Request timed out",
        "Error: ",
        # legacy format with emoji
        "❌ Error:",
        "❌",
    ],
    "progress": [
        "I'll start by fetching",
        "I'll generate",
        "Let me start",
        "Let me fetch",
        "Let me check",
        "Let me look",
        "Let me generate",
        "Let me work",
        "I can see",
        "I have enough data",
        "I have the full",
        "Now I have",
        "This is a continuation",
        "Full EOD summary generated",
        "Continuing with the project",
        "Looking into this",
        "Handed off to Tony",
        "On it —",
        "I found some matching",
    ],
    "upload_ack": [
        "EOD Summary —",
        "Full HTML report attached",
        "Full report uploaded",
    ],
    "html_content": [
        "```\n<!DOCTYPE html>",
        "```\n<html",
        "```\n<head>",
        "```\n    <meta",
        "<!DOCTYPE html>",
        "<html lang=",
        "olor: #6b7280",        # partial inline HTML split across messages
        "li>\n</ul></div>",
        "font-size: 12px; margin-top",  # report footer fragment
        "End of report —</p>",
    ],
    "timing": [
        "_(", "_({",
    ],
}


def matches_type(text: str, msg_type: str) -> bool:
    if msg_type == "all_bot":
        return True
    return any(p in text for p in TYPE_PATTERNS.get(msg_type, []))


def should_delete(text: str) -> bool:
    return any(matches_type(text, t) for t in DELETE_TYPES)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK_BOT_TOKEN not set.")
        sys.exit(1)

    client  = WebClient(token=token)
    deleted = 0
    skipped = 0

    print(f"Fetching last {LIMIT} messages from {CHANNEL}...")
    print(f"Delete types: {DELETE_TYPES}")
    print(f"Dry run: {DRY_RUN}\n")

    response = client.conversations_history(channel=CHANNEL, limit=LIMIT)
    messages = response.get("messages") or []

    for msg in messages:
        if not (msg.get("bot_id") or msg.get("subtype") == "bot_message"):
            continue

        text = msg.get("text", "")
        ts   = msg["ts"]

        if not should_delete(text):
            skipped += 1
            continue

        preview = text[:80].replace("\n", " ")
        if DRY_RUN:
            print(f"[DRY RUN] would delete [{ts}]: {preview}")
            deleted += 1
        else:
            try:
                client.chat_delete(channel=CHANNEL, ts=ts)
                print(f"Deleted [{ts}]: {preview}")
                deleted += 1
                time.sleep(0.5)   # stay under Slack rate limit
            except Exception as e:
                print(f"Failed [{ts}]: {e}")

    print(f"\nDone. Deleted: {deleted} | Skipped (no match): {skipped}")
    if DRY_RUN:
        print("Set DRY_RUN = False to actually delete.")


if __name__ == "__main__":
    main()