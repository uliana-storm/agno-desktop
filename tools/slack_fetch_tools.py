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
from collections import Counter
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from agno.tools.toolkit import Toolkit
from slack_sdk.errors import SlackApiError

from bot.debug import agent_debug_enabled
from tools.slack_helpers import (
    bot_client,
    clean_text,
    resolve_channel,
    resolve_user_names,
    ts_to_time,
    ts_to_datetime,
)

_MAX_PAGES   = 5
_PAGE_SIZE   = 200
_MAX_REPLIES = 10
_MELBOURNE   = ZoneInfo("Australia/Melbourne")


def _fetch_debug(msg: str) -> None:
    if agent_debug_enabled():
        print(f"[fetch_digest] {msg}", flush=True)


def _filter_stats(raw: list[dict]) -> tuple[int, dict[str, int]]:
    """Count why raw messages were excluded by the filter."""
    no_text = 0
    excluded_subtype: Counter[str] = Counter()
    for m in raw:
        subtype = m.get("subtype")
        if subtype in ("channel_join", "channel_leave", "bot_message"):
            excluded_subtype[subtype or "none"] += 1
        elif not m.get("text"):
            no_text += 1
    return no_text, dict(excluded_subtype)


def _parse_fetch_window(hours: int, date: str) -> tuple[float, Optional[float], str, Optional[str]]:
    """Return (oldest, latest, window_label, error). latest is None when open-ended."""
    date = date.strip()
    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=_MELBOURNE)
        except ValueError:
            return 0.0, None, "", f"Invalid date {date!r} — use YYYY-MM-DD (Australia/Melbourne)."
        oldest = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        latest = day.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()
        return oldest, latest, f"{date} (Melbourne)", None

    oldest = time.time() - (hours * 3600)
    return oldest, None, f"last {hours}h", None


def _conversations_history(
    client,
    channel_id: str,
    oldest: float,
    latest: Optional[float] = None,
    *,
    limit: int = _PAGE_SIZE,
    cursor: Optional[str] = None,
):
    kwargs: dict = {
        "channel": channel_id,
        "oldest": str(oldest),
        "limit": limit,
    }
    if latest is not None:
        kwargs["latest"] = str(latest)
    if cursor:
        kwargs["cursor"] = cursor
    return client.conversations_history(**kwargs)


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
    latest: Optional[float] = None,
) -> tuple[list[dict], str]:
    """
    Fetch and format messages from one channel within oldest/latest bounds.
    Returns (messages, error_string). messages is [] on error.
    """
    display = f"#{channel_name}" if channel_name else channel_id
    raw: list[dict] = []
    cursor: Optional[str] = None
    pages = 0

    try:
        while pages < _MAX_PAGES:
            response = _conversations_history(
                client, channel_id, oldest, latest, cursor=cursor
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
            msg = (
                f"Bot is not a member of {display}. "
                f"Invite the bot first: /invite @<bot_name> in {display}."
            )
            _fetch_debug(f"EXIT error channel={channel_id} display={display} code={code}")
            return [], msg
        err = f"Slack API error for {display}: {code}"
        _fetch_debug(f"EXIT error channel={channel_id} display={display} code={code}")
        return [], err
    except Exception as e:
        err = f"Unexpected error fetching {display}: {e}"
        _fetch_debug(f"EXIT error channel={channel_id} display={display} exc={e!r}")
        return [], err

    if not raw and pages == 1:
        _fetch_debug(
            f"retry empty_raw channel={channel_id} display={display} "
            f"pages={pages} sleeping=1.5s"
        )
        time.sleep(1.5)
        try:
            response = _conversations_history(client, channel_id, oldest, latest)
            raw = response.get("messages") or []
            if raw:
                _fetch_debug(
                    f"retry ok channel={channel_id} display={display} raw={len(raw)}"
                )
        except SlackApiError:
            pass

    if not raw:
        oldest_iso = datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat()
        _fetch_debug(
            f"EXIT empty_raw channel={channel_id} display={display} "
            f"pages={pages} oldest={oldest} ({oldest_iso})"
        )
        return [], ""

    filtered = [
        m for m in raw
        if m.get("text") and m.get("subtype") not in
        ("channel_join", "channel_leave", "bot_message")
    ]
    if not filtered:
        no_text, subtypes = _filter_stats(raw)
        _fetch_debug(
            f"EXIT empty_filter channel={channel_id} display={display} "
            f"raw={len(raw)} pages={pages} no_text={no_text} excluded_subtypes={subtypes}"
        )
        return [], ""

    human_ids  = list({m["user"] for m in filtered if m.get("user")})
    user_names = resolve_user_names(client, human_ids)

    format_ts = ts_to_datetime if latest is not None else ts_to_time
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
            "time":         format_ts(ts),
            "user":         msg.get("username") or user_names.get(uid, uid) or "unknown",
            "text":         clean_text(msg.get("text", "")),
            "channel_name": channel_name or channel_id,
            "replies":      replies,
        })
    _fetch_debug(
        f"EXIT ok channel={channel_id} display={display} "
        f"raw={len(raw)} filtered={len(filtered)} pages={pages} out={len(messages)}"
    )
    return messages, ""


def _render_line(msg: dict, include_channel: bool) -> list[str]:
    ch    = f"#{msg['channel_name']} | " if include_channel else ""
    lines = [f"[{ch}{msg['time']}] {msg['user']}: {msg['text']}"]
    lines.extend(msg["replies"])
    return lines


def _render_stream(
    all_messages: list[dict],
    channel_labels: list[str],
    window_label: str,
) -> str:
    sorted_msgs  = sorted(all_messages, key=lambda m: m["ts"])
    channels_str = " + ".join(f"#{c}" for c in channel_labels)
    lines        = [
        f"=== {channels_str} — {window_label} | {len(sorted_msgs)} messages ===\n"
    ]
    multi = len(channel_labels) > 1
    for msg in sorted_msgs:
        lines.extend(_render_line(msg, include_channel=multi))
        if msg["replies"]:
            lines.append("")
    return "\n".join(lines)


def _render_blocks(
    messages_by_channel: dict[str, list[dict]],
    window_label: str,
) -> str:
    sections = []
    for channel_name, msgs in messages_by_channel.items():
        sorted_msgs = sorted(msgs, key=lambda m: m["ts"])
        lines       = [
            f"=== #{channel_name} — {window_label} | {len(sorted_msgs)} messages ===\n"
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
        date: str = "",
        format: str = "stream",
    ) -> str:
        """
        Fetch and compress Slack channel activity into plain text.

        Resolves user IDs to display names. Includes thread replies.
        Returns a clear error if the bot is not a member of a channel.

        Args:
            channels: Comma-separated channel IDs (C...) or names (dev, #dev).
                      Examples: "dev"  |  "dev,general,requests"
            hours:    How far back to fetch. Default 24. Ignored when date is set.
                      Examples: 1 (last hour), 8 (workday), 168 (last week).
            date:     Specific calendar day as YYYY-MM-DD in Australia/Melbourne.
                      Use for "messages on May 25" — do not approximate with hours.
            format:   "stream" — combined chronological feed across all channels.
                                  Best for EOD summaries and cross-channel analysis.
                      "blocks" — one labeled section per channel.
                                  Best for focused per-channel analysis.
        """
        if format not in ("stream", "blocks"):
            format = "stream"

        oldest, latest, window_label, window_err = _parse_fetch_window(hours, date)
        if window_err:
            return window_err

        oldest_iso = datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat()
        latest_iso = (
            datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()
            if latest is not None
            else None
        )
        channel_list = [c.strip() for c in channels.split(",") if c.strip()]

        if not channel_list:
            return "No channels specified."

        _fetch_debug(
            f"call channels={channels!r} hours={hours} date={date!r} "
            f"oldest={oldest} ({oldest_iso}) latest={latest} ({latest_iso}) "
            f"window={window_label!r} format={format!r}"
        )

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
                client,
                channel_id,
                channel_name,
                oldest,
                self.min_reply_count,
                latest=latest,
            )

            _fetch_debug(
                f"aggregate label={label!r} channel_id={channel_id} "
                f"messages={len(messages)} error={error!r}"
            )

            if error:
                errors.append(error)
                continue

            if not messages:
                errors.append(f"#{label}: no messages for {window_label}.")
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
                output_parts.append(_render_blocks(messages_by_channel, window_label))
            else:
                output_parts.append(_render_stream(all_messages, channel_labels, window_label))

        if not all_messages and not errors:
            return f"No messages found for {window_label}."

        return "\n\n".join(output_parts)