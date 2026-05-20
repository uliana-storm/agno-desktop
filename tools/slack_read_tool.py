"""Slack read helpers complementing Agno SlackTools — today-scoped history and search.

Agno SlackTools: https://docs.agno.com/tools/toolkits/social/slack
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from agno.tools.toolkit import Toolkit
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]+$")
_MAX_PAGES = 10
_PAGE_SIZE = 200


def _bot_client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not set")
    return WebClient(token=token)


def _search_client() -> WebClient | None:
    token = os.environ.get("SLACK_USER_TOKEN", "")
    if not token:
        return None
    return WebClient(token=token)


def _resolve_channel(client: WebClient, channel: str) -> tuple[str, str | None]:
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


def _resolve_user_names(client: WebClient, user_ids: list[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    for uid in user_ids:
        if not uid:
            continue
        try:
            resp = client.users_info(user=uid)
            user = resp.get("user") or {}
            profile = user.get("profile") or {}
            names[uid] = profile.get("display_name") or profile.get("real_name") or uid
        except SlackApiError:
            names[uid] = uid
    return names


def _format_message(msg: dict[str, Any], user_names: dict[str, str], channel_id: str, channel_name: str | None) -> dict[str, Any]:
    user_id = msg.get("user", "")
    entry: dict[str, Any] = {
        "text": msg.get("text", ""),
        "user": msg.get("username") or user_names.get(user_id, user_id) or "unknown",
        "ts": msg.get("ts", ""),
        "channel_id": channel_id,
    }
    if channel_name:
        entry["channel_name"] = channel_name
    if msg.get("thread_ts"):
        entry["thread_ts"] = msg["thread_ts"]
        entry["reply_count"] = msg.get("reply_count", 0)
    return entry


def _start_of_day_ts(timezone: str) -> str:
    tz = ZoneInfo(timezone)
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return str(start.timestamp())


class SlackReadToolkit(Toolkit):
    """Extra Slack read tools for date-scoped history and message search."""

    def __init__(self) -> None:
        super().__init__(name="slack_read")
        self.register(self.get_messages_since_today)
        self.register(self.search_slack_messages)

    def get_messages_since_today(
        self,
        channel: str,
        timezone: str = "Australia/Melbourne",
    ) -> str:
        """Fetch top-level channel messages since local midnight.

        Use when the user asks for today's chat, messages since this morning,
        or an end-of-day style summary for the current day.

        Args:
            channel: Slack channel ID (C…) or name (#general).
            timezone: IANA timezone for start-of-day (default Australia/Melbourne).

        Returns:
            JSON with messages list, count, and since_ts boundary.
        """
        try:
            client = _bot_client()
            channel_id, channel_name = _resolve_channel(client, channel)
            oldest = _start_of_day_ts(timezone)

            raw_messages: list[dict[str, Any]] = []
            cursor: Optional[str] = None
            pages = 0

            while pages < _MAX_PAGES:
                response = client.conversations_history(
                    channel=channel_id,
                    limit=_PAGE_SIZE,
                    cursor=cursor,
                    oldest=oldest,
                )
                batch = response.get("messages") or []
                raw_messages.extend(batch)
                cursor = (response.get("response_metadata") or {}).get("next_cursor")
                pages += 1
                if not cursor:
                    break

            human_ids = [
                m["user"]
                for m in raw_messages
                if m.get("user") and m.get("subtype") != "bot_message"
            ]
            user_names = _resolve_user_names(client, list(set(human_ids)))

            messages = [
                _format_message(m, user_names, channel_id, channel_name)
                for m in raw_messages
                if m.get("text") is not None
            ]

            return json.dumps(
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "since_ts": oldest,
                    "timezone": timezone,
                    "count": len(messages),
                    "messages": messages,
                }
            )
        except SlackApiError as e:
            return json.dumps({"error": e.response.get("error", str(e)), "channel": channel})
        except Exception as e:
            return json.dumps({"error": str(e), "channel": channel})

    def search_slack_messages(
        self,
        query: str,
        channel: str = "",
        limit: int = 20,
    ) -> str:
        """Search Slack messages using workspace search or channel scan.

        With SLACK_USER_TOKEN set, uses Slack search API and supports modifiers:
        from:@user, in:#channel, has:link, before:YYYY-MM-DD, after:YYYY-MM-DD.

        Without user token, scans recent channel history (limit 100) and filters
        by text match — pass channel ID/name when searching a specific channel.

        Args:
            query: Search text and/or Slack search modifiers.
            channel: Channel ID or name — used for fallback scan; add in:#channel to query for user-token search.
            limit: Max results (default 20, max 100).

        Returns:
            JSON with count and matching messages.
        """
        limit = min(max(limit, 1), 100)
        search_client = _search_client()

        if search_client:
            try:
                full_query = query.strip()
                if channel and "in:" not in full_query.lower():
                    ch = channel if channel.startswith("#") else f"#{channel.removeprefix('#')}"
                    if _CHANNEL_ID_RE.match(channel.strip()):
                        full_query = f"{full_query} in:{channel.strip()}"
                    else:
                        full_query = f"{full_query} in:{ch}"
                response = search_client.search_messages(query=full_query, count=limit)
                matches = (response.get("messages") or {}).get("matches") or []
                messages = [
                    {
                        "text": m.get("text", ""),
                        "user": m.get("username") or m.get("user", "unknown"),
                        "channel_id": (m.get("channel") or {}).get("id", ""),
                        "channel_name": (m.get("channel") or {}).get("name", ""),
                        "ts": m.get("ts", ""),
                        "permalink": m.get("permalink", ""),
                    }
                    for m in matches
                ]
                return json.dumps({"count": len(messages), "query": full_query, "messages": messages})
            except SlackApiError as e:
                return json.dumps({"error": e.response.get("error", str(e)), "query": query})

        if not channel.strip():
            return json.dumps(
                {
                    "error": "channel_required",
                    "hint": "Without SLACK_USER_TOKEN, pass channel for local search. "
                    "Or set SLACK_USER_TOKEN for workspace search with in:#channel modifiers.",
                }
            )

        try:
            history_raw = self.get_messages_since_today(channel)
            history = json.loads(history_raw)
            if history.get("error"):
                history = json.loads(self._get_channel_history_fallback(channel, 100))
            messages = history.get("messages") or []
            q = query.lower()
            terms = [t for t in re.split(r"\s+", q) if t and t not in ("in:#channel",)]
            filtered = [
                m
                for m in messages
                if any(term in (m.get("text") or "").lower() for term in terms)
            ][:limit]
            return json.dumps(
                {
                    "count": len(filtered),
                    "query": query,
                    "mode": "channel_scan_fallback",
                    "messages": filtered,
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e), "query": query})

    def _get_channel_history_fallback(self, channel: str, limit: int) -> str:
        """Fetch last N messages via bot token when today-scoped fetch fails."""
        client = _bot_client()
        channel_id, channel_name = _resolve_channel(client, channel)
        response = client.conversations_history(channel=channel_id, limit=limit)
        raw = response.get("messages") or []
        user_names = _resolve_user_names(
            client, list({m["user"] for m in raw if m.get("user")})
        )
        messages = [_format_message(m, user_names, channel_id, channel_name) for m in raw]
        return json.dumps({"count": len(messages), "messages": messages})
