"""
slack_fetch_tool.py

Fetches Slack channel history and returns a compressed plain-text digest.
Full message objects never enter Tony's context.

Supports multiple channels, flexible time windows, and two output formats:
  stream — combined chronological feed across all channels (default)
  blocks — one labeled section per channel

Thread replies included for messages with reply_count >= min_reply_count.
User IDs resolved to display names inside the tool.
Bot access checked per channel — clear error returned if not a member.
"""

import time
from typing import Optional

from agno.tools.toolkit import Toolkit
from slack_sdk.errors import SlackApiError

from tools.slack_helpers import (
    bot_client,
    clean_text,
    resolve_channel,
    resolve_user_names,
    ts_to_time,
)

_MAX_PAGES   = 5
_PAGE_SIZE   = 200
_MAX_REPLIES = 10


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _fetch_replies(
    client,
    channel_id: str,
    thread_ts: str,
    user_names: dict[str, str],
) -> list[str]:
    """Fetch thread replies, resolve any new user IDs, return plain text lines."""
    try:
        response = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=_MAX_REPLIES + 1,
        )
        messages = (response.get("messages") or [])[1:]  # skip parent

        new_ids = [
            m["user"] for m in messages
            if m.get("user") and m["user"] not in user_names
        ]
        if new_ids:
            user_names.update(resolve_user_names(client, list(set(new_ids))))

        lines = []
        for msg in messages[:_MAX_REPLIES]:
            uid  = msg.get("user", "")
            name = msg.get("username") or user_names.get(uid, uid) or "unknown"
            text = clean_text(msg.get("text", ""))
            if text:
                lines.append(f"  └ {name}: {text}")
        return lines
    except SlackApiError:
        return []


def _fetch_channel(
    client,
    channel_id: str,
    channel_name: Optional[str],
    oldest: float,
    min_reply_count: int,
) -> tuple[list[dict], str]:
    """
    Fetch and format messages from one channel since oldest timestamp.
    Returns (messages, error_string). messages is [] on error.
    """
    display = f"#{channel_name}" if channel_name else channel_id
    raw: list[dict] = []
    cursor: Optional[str] = None
    pages = 0

    try:
        while pages < _MAX_PAGES:
            response = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest),
                limit=_PAGE_SIZE,
                cursor=cursor,
            )
            batch = response.get("messages") or []
            raw.extend(batch)
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            pages += 1
            if not cursor:
                break
    except SlackApiError as e:
        code = e.response.get("error", str(e))
        if code in ("not_in_channel", "channel_not_found", "missing_scope"):
            return [], (
                f"Bot is not a member of {display}. "
                f"Invite the bot first: /invite @<bot_name> in {display}."
            )
        return [], f"Slack API error for {display}: {code}"
    except Exception as e:
        return [], f"Unexpected error fetching {display}: {e}"

    if not raw:
        return [], ""

    filtered = [
        m for m in raw
        if m.get("text") and m.get("subtype") not in
        ("channel_join", "channel_leave", "bot_message")
    ]
    if not filtered:
        return [], ""

    human_ids  = list({m["user"] for m in filtered if m.get("user")})
    user_names = resolve_user_names(client, human_ids)

    messages = []
    for msg in reversed(filtered):  # chronological (Slack returns newest-first)
        ts  = msg.get("ts", "")
        uid = msg.get("user", "")
        rc  = int(msg.get("reply_count", 0))

        replies: list[str] = []
        if rc >= min_reply_count and msg.get("thread_ts"):
            replies = _fetch_replies(client, channel_id, msg["thread_ts"], user_names)

        messages.append({
            "ts":           float(ts) if ts else 0.0,
            "time":         ts_to_time(ts),
            "user":         msg.get("username") or user_names.get(uid, uid) or "unknown",
            "text":         clean_text(msg.get("text", "")),
            "channel_name": channel_name or channel_id,
            "replies":      replies,
        })
    return messages, ""


def _render_line(msg: dict, include_channel: bool) -> list[str]:
    ch    = f"#{msg['channel_name']} | " if include_channel else ""
    lines = [f"[{ch}{msg['time']}] {msg['user']}: {msg['text']}"]
    lines.extend(msg["replies"])
    return lines


def _render_stream(
    all_messages: list[dict],
    channel_labels: list[str],
    hours: int,
) -> str:
    sorted_msgs  = sorted(all_messages, key=lambda m: m["ts"])
    channels_str = " + ".join(f"#{c}" for c in channel_labels)
    lines        = [
        f"=== {channels_str} — last {hours}h | {len(sorted_msgs)} messages ===\n"
    ]
    multi = len(channel_labels) > 1
    for msg in sorted_msgs:
        lines.extend(_render_line(msg, include_channel=multi))
        if msg["replies"]:
            lines.append("")
    return "\n".join(lines)


def _render_blocks(
    messages_by_channel: dict[str, list[dict]],
    hours: int,
) -> str:
    sections = []
    for channel_name, msgs in messages_by_channel.items():
        sorted_msgs = sorted(msgs, key=lambda m: m["ts"])
        lines       = [
            f"=== #{channel_name} — last {hours}h | {len(sorted_msgs)} messages ===\n"
        ]
        for msg in sorted_msgs:
            lines.extend(_render_line(msg, include_channel=False))
            if msg["replies"]:
                lines.append("")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Toolkit
# ---------------------------------------------------------------------------

class SlackFetchToolkit(Toolkit):
    """
    Fetch Slack channel history as a compressed plain-text digest.

    Use for EOD summaries, research context, or any task requiring
    a structured view of recent channel activity across one or more channels.

    Full message objects never enter Tony's context — only plain text digests.
    """

    def __init__(self, min_reply_count: int = 1, **kwargs):
        self.min_reply_count = min_reply_count
        super().__init__(
            name="slack_fetch",
            tools=[self.fetch_digest],
            **kwargs,
        )

    def fetch_digest(
        self,
        channels: str,
        hours: int = 24,
        format: str = "stream",
    ) -> str:
        """
        Fetch and compress Slack channel activity into plain text.

        Resolves user IDs to display names. Includes thread replies.
        Returns a clear error if the bot is not a member of a channel.

        Args:
            channels: Comma-separated channel IDs (C...) or names (dev, #dev).
                      Examples: "dev"  |  "dev,general,requests"
            hours:    How far back to fetch. Default 24.
                      Examples: 1 (last hour), 8 (workday), 168 (last week).
            format:   "stream" — combined chronological feed across all channels.
                                  Best for EOD summaries and cross-channel analysis.
                      "blocks" — one labeled section per channel.
                                  Best for focused per-channel analysis.
        """
        if format not in ("stream", "blocks"):
            format = "stream"

        oldest       = time.time() - (hours * 3600)
        channel_list = [c.strip() for c in channels.split(",") if c.strip()]

        if not channel_list:
            return "No channels specified."

        try:
            client = bot_client()
        except ValueError as e:
            return f"Slack client error: {e}"

        all_messages:        list[dict]      = []
        messages_by_channel: dict[str, list] = {}
        channel_labels:      list[str]       = []
        errors:              list[str]       = []

        for raw_channel in channel_list:
            try:
                channel_id, channel_name = resolve_channel(client, raw_channel)
            except ValueError:
                errors.append(
                    f"Channel '{raw_channel}' not found. "
                    f"Check the name or invite the bot first."
                )
                continue
            except SlackApiError as e:
                errors.append(
                    f"Could not resolve '{raw_channel}': "
                    f"{e.response.get('error', str(e))}"
                )
                continue

            label    = channel_name or channel_id
            messages, error = _fetch_channel(
                client, channel_id, channel_name, oldest, self.min_reply_count
            )

            if error:
                errors.append(error)
                continue

            if not messages:
                errors.append(f"#{label}: no messages in the last {hours}h.")
                continue

            all_messages.extend(messages)
            messages_by_channel[label] = messages
            channel_labels.append(label)

        output_parts: list[str] = []

        if errors:
            output_parts.append(
                "Issues:\n" + "\n".join(f"• {e}" for e in errors)
            )

        if all_messages:
            if format == "blocks":
                output_parts.append(_render_blocks(messages_by_channel, hours))
            else:
                output_parts.append(_render_stream(all_messages, channel_labels, hours))

        if not all_messages and not errors:
            return f"No messages found in the last {hours}h."

        return "\n\n".join(output_parts)