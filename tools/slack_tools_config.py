"""Shared Agno SlackTools configuration for Jarvis and Tony.

See: https://docs.agno.com/examples/tools/slack-tools
"""

import os

from agno.tools.slack import SlackTools


def jarvis_slack_tools() -> SlackTools:
    """Slack read + targeted post for Jarvis (thread replies and channel broadcasts)."""
    return SlackTools(
        token=os.environ.get("SLACK_BOT_TOKEN", ""),
        enable_send_message=True,
        enable_send_message_thread=True,
        enable_list_channels=True,
        enable_get_channel_history=True,
        enable_get_thread=True,
        enable_list_users=True,
        enable_get_user_info=True,
        enable_get_channel_info=True,
        enable_upload_file=False,
        enable_download_file=False,
        enable_search_messages=False,
        enable_search_workspace=False,
        thread_message_limit=100,
        add_instructions=False,
    )


def tony_slack_tools() -> SlackTools:
    """Slack read + post + upload for Tony (EOD reports and deliverables)."""
    return SlackTools(
        token=os.environ.get("SLACK_BOT_TOKEN", ""),
        enable_send_message=True,
        enable_send_message_thread=True,
        enable_list_channels=True,
        enable_get_channel_history=True,
        enable_get_thread=True,
        enable_list_users=True,
        enable_get_user_info=True,
        enable_get_channel_info=True,
        enable_upload_file=True,
        enable_download_file=False,
        enable_search_messages=False,
        enable_search_workspace=False,
        thread_message_limit=100,
        add_instructions=False,
    )
