"""Slack channel post tool — Jarvis only."""

from bot.slack_notify import post_to_slack_channel


def post_to_slack_channel_tool(
    channel: str,
    text: str,
    thread_ts: str = "",
) -> str:
    """Post a message to a Slack channel or thread.

    Use for broadcast/cross-channel posts (e.g. send summary to #dev).
    Do NOT use for normal replies — the stream handles those.

    Args:
        channel: Slack channel ID (e.g. C083X87KF9Q) — look up in Known Slack channels.
        text: Message body (Slack mrkdwn).
        thread_ts: Optional thread timestamp. Omit for top-level channel post.
    """
    post_to_slack_channel(channel, text, thread_ts or None)
    return f"Posted to {channel}."
