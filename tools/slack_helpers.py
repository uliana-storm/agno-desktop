"""
slack_helpers.py

Shared Slack utilities imported by slack_fetch_tool and slack_search_tool.
No Agno toolkit here — pure helpers only.
"""

import os
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]+$")
_MAX_MSG_CHARS = 300
_MELBOURNE = ZoneInfo("Australia/Melbourne")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def bot_client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not set")
    return WebClient(token=token)


def search_client() -> WebClient:
    token = os.environ.get("SLACK_USER_TOKEN", "")
    if not token:
        raise ValueError(
            "SLACK_USER_TOKEN is not set. "
            "Slack search requires a user token with search:read scope."
        )
    return WebClient(token=token)


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

def resolve_channel(client: WebClient, channel: str) -> tuple[str, str | None]:
    """
    Resolve channel name or ID to (channel_id, channel_name).
    Accepts: C083X87KF9Q, dev, #dev
    Raises ValueError if not found.
    """
    raw = channel.strip()
    if _CHANNEL_ID_RE.match(raw):
        return raw, None
    name = raw.removeprefix("#").lower()
    cursor: Optional[str] = None
    types = "public_channel,private_channel"
    while True:
        try:
            response = client.conversations_list(
                types=types, limit=1000, cursor=cursor, exclude_archived=True
            )
        except SlackApiError as e:
            if "private" in types and e.response.get("error") == "missing_scope":
                types = "public_channel"
                cursor = None
                continue
            raise
        for ch in response.get("channels") or []:
            ch_id, ch_name = ch.get("id"), ch.get("name")
            if ch_id and ch_name and ch_name.lower() == name:
                return ch_id, ch_name
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    raise ValueError(f"channel_not_found: {channel}")


def resolve_user_names(client: WebClient, user_ids: list[str]) -> dict[str, str]:
    """Batch-resolve Slack user IDs to display names."""
    names: dict[str, str] = {}
    for uid in user_ids:
        if not uid:
            continue
        try:
            resp    = client.users_info(user=uid)
            user    = resp.get("user") or {}
            profile = user.get("profile") or {}
            names[uid] = profile.get("display_name") or profile.get("real_name") or uid
        except SlackApiError:
            names[uid] = uid
    return names


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def ts_to_time(ts: str) -> str:
    """Convert Slack timestamp to HH:MM string (Australia/Melbourne)."""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=_MELBOURNE)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError, OSError):
        return "??:??"


def ts_to_datetime(ts: str) -> str:
    """Convert Slack timestamp to DD/MM HH:MM string (Australia/Melbourne)."""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=_MELBOURNE)
        return dt.strftime("%d/%m %H:%M")
    except (ValueError, TypeError, OSError):
        return "??:??"


def clean_text(text: str) -> str:
    """Collapse whitespace and cap message text at _MAX_MSG_CHARS."""
    cleaned = " ".join(text.split())
    if len(cleaned) > _MAX_MSG_CHARS:
        return cleaned[:_MAX_MSG_CHARS] + "…"
    return cleaned