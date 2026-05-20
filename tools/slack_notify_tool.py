"""Slack post tool — lets agents deliver scheduled-run results to Slack."""

from bot.slack_notify import post_to_slack_channel


def post_to_slack(
    channel: str,
    text: str,
    thread_ts: str = "",
) -> str:
    """Post a message to the Slack channel or thread where the user is waiting.

    Use this at the end of a scheduled run to deliver your response.
    Include slack_channel and thread_ts from the schedule payload when present.

    Args:
        channel: Slack channel ID from the schedule payload (slack_channel).
        text: Message to send (plain Slack text, under 300 words unless detail was requested).
        thread_ts: Thread timestamp from the schedule payload (thread_ts). Leave empty for channel root.

    Returns:
        Confirmation string.
    """
    post_to_slack_channel(channel, text, thread_ts or None)
    return "Posted to Slack."
