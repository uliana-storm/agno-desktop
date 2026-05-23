"""
slack_search_tool.py

Intent-based Slack message search via Slack's native search API.
Requires SLACK_USER_TOKEN with search:read scope.

Translates structured parameters (username, channel, content, time window)
into Slack search modifiers server-side — no local scanning, no history fetching.

Slack search modifiers used:
  content    → keyword terms
  username   → from:@username
  channel    → in:#channel
  hours      → after:YYYY-MM-DD  (date-granular — sub-day not supported)
  after_time → noted in output, not enforced (Slack limitation)
"""

from datetime import datetime, timedelta, timezone

from agno.tools.toolkit import Toolkit
from slack_sdk.errors import SlackApiError

from tools.slack_helpers import (
    bot_client,
    clean_text,
    resolve_channel,
    search_client,
    ts_to_time,
)

_MAX_RESULTS   = 50
_CHANNEL_ID_RE_STR = r"^[CGD][A-Z0-9]+$"

import re
_CHANNEL_ID_RE = re.compile(_CHANNEL_ID_RE_STR)


# ---------------------------------------------------------------------------
# Toolkit
# ---------------------------------------------------------------------------

class SlackSearchToolkit(Toolkit):
    """
    Intent-based Slack message search.

    Resolves user, channel, content, and time intent into Slack search
    modifiers and runs the query server-side via SLACK_USER_TOKEN.

    Distinct from SlackFetchToolkit — no history fetching, no pagination,
    no thread traversal. Pure search against Slack's search index.
    """

    def __init__(self, **kwargs):
        super().__init__(
            name="slack_search",
            tools=[self.search_messages],
            **kwargs,
        )

    def search_messages(
        self,
        content: str = "",
        username: str = "",
        channel: str = "",
        hours: int = 24,
        after_time: str = "",
    ) -> str:
        """
        Search Slack messages by intent.

        Translates structured parameters into Slack search modifiers and
        queries Slack's search API server-side. No local filtering.

        Args:
            content:    Keywords to find in message text.
                        Examples: "deployment failed", "PR review", "blocked"
            username:   Sender display name — partial match accepted.
                        Examples: "alice", "bob"
                        Becomes: from:@alice
            channel:    Channel name or ID to scope the search.
                        Examples: "dev", "#general", "C083X87KF9Q"
                        Becomes: in:#dev
            hours:      How far back to search. Default 24.
                        Examples: 1, 8, 48, 168 (last week)
                        Becomes: after:YYYY-MM-DD
                        Note: Slack search is date-granular — results cover
                        the full day, not a precise hour cutoff.
            after_time: "HH:MM" intent annotation (e.g. "15:00").
                        Noted in results but not enforced — Slack search does
                        not support sub-day time filtering. Surface this
                        limitation to the user if they asked for a specific
                        time window within a day.

        Returns:
            Plain text — one result per line: [#channel | HH:MM] user: text  permalink
            Returns an error string if SLACK_USER_TOKEN is not set or search fails.
        """
        try:
            client = search_client()
        except ValueError as e:
            return str(e)

        # Build query from structured intent
        parts: list[str] = []

        if content.strip():
            parts.append(content.strip())

        if username.strip():
            clean_user = username.strip().lstrip("@")
            parts.append(f"from:@{clean_user}")

        cutoff_date = ""
        if hours:
            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).strftime("%Y-%m-%d")
            parts.append(f"after:{cutoff_date}")

        if channel.strip():
            ch = channel.strip()
            # Resolve channel ID to name — Slack search needs #name not ID
            if _CHANNEL_ID_RE.match(ch):
                try:
                    bc = bot_client()
                    _, ch_name = resolve_channel(bc, ch)
                    ch = ch_name or ch
                except Exception:
                    pass
            parts.append(f"in:#{ch.removeprefix('#')}")

        if not parts:
            return (
                "Provide at least one search parameter: "
                "content, username, channel, or hours."
            )

        query = " ".join(parts)

        # Sub-day time note — honest about Slack's limitation
        time_note = ""
        if after_time.strip():
            time_note = (
                f"\nNote: after_time '{after_time}' recorded — Slack search "
                f"filters by date only (after:{cutoff_date}), not by hour. "
                f"Results cover the full day."
            )

        try:
            response = client.search_messages(query=query, count=_MAX_RESULTS)
            matches  = (response.get("messages") or {}).get("matches") or []

            if not matches:
                return f"No results for: {query}{time_note}"

            lines = [f"Search: '{query}' — {len(matches)} results{time_note}\n"]
            for m in matches:
                ch_name = (m.get("channel") or {}).get("name", "?")
                user    = m.get("username") or m.get("user", "unknown")
                t       = ts_to_time(m.get("ts", ""))
                text    = clean_text(m.get("text", ""))
                link    = m.get("permalink", "")
                entry   = f"[#{ch_name} | {t}] {user}: {text}"
                if link:
                    entry += f"  {link}"
                lines.append(entry)

            return "\n".join(lines)

        except SlackApiError as e:
            return (
                f"Search error: {e.response.get('error', str(e))} "
                f"— query: {query}"
            )
        except Exception as e:
            return f"Unexpected search error: {e}"