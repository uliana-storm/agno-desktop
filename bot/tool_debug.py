"""Tool result formatting for Slack debug posts."""

from bot.debug import agent_debug_enabled


def format_tool_completion_message(tool_name: str, result: str) -> str:
    """
    Format a tool completion event for a Slack debug post.
    Returns empty string if debug is disabled or result is empty.
    Only used when the tool completion block in agent_runner.py is uncommented.
    """
    if not agent_debug_enabled():
        return ""
    if not result or not result.strip():
        return ""
    preview = result.strip()[:200].replace("\n", " ")
    return f"✅ `{tool_name}` → {preview}"