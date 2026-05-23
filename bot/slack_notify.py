"""Slack notification helpers for scheduled agent runs."""

import os
import threading
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_client: Optional[WebClient] = None
_client_lock = threading.Lock()


def _get_client() -> WebClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking
                token = os.environ.get("SLACK_BOT_TOKEN", "")
                if not token:
                    raise RuntimeError("SLACK_BOT_TOKEN is not set")
                _client = WebClient(token=token)
    return _client


def post_to_slack_channel(
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
) -> None:
    """Post a message to a Slack channel or thread.

    Args:
        channel: Slack channel ID (e.g. C01234567) or DM channel ID.
        text: Message body (Slack mrkdwn).
        thread_ts: Optional thread timestamp to reply in a thread.
    """
    kwargs: dict = {"channel": channel, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    try:
        _get_client().chat_postMessage(**kwargs)
    except SlackApiError as e:
        raise RuntimeError(f"Slack API error: {e.response['error']}") from e
