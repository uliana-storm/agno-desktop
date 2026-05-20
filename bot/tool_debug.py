"""Format tool results for Slack debug posts."""

import json

_SLACK_READ_TOOLS = frozenset(
    {
        "get_thread",
        "get_channel_history",
        "list_channels",
        "get_channel_info",
        "list_users",
        "get_user_info",
        "search_messages",
        "search_workspace",
        "send_message",
        "send_message_thread",
        "search_slack_messages",
        "get_messages_since_today",
    }
)

_PREVIEW_MAX = 900


def _preview_messages(messages: list, limit: int = 6) -> str:
    lines = []
    for msg in messages[:limit]:
        if not isinstance(msg, dict):
            continue
        user = msg.get("user") or msg.get("author") or "?"
        text = (msg.get("text") or msg.get("content") or "").replace("\n", " ").strip()
        if len(text) > 100:
            text = text[:100] + "…"
        lines.append(f"• {user}: {text}")
    if len(messages) > limit:
        lines.append(f"• … +{len(messages) - limit} more")
    return "\n".join(lines)


def _summarize_slack_json(tool_name: str, data: dict | list) -> str:
    if isinstance(data, list):
        if tool_name == "get_channel_history" or (data and isinstance(data[0], dict) and "text" in data[0]):
            header = f"{len(data)} top-level message(s)"
            body = _preview_messages(data)
            return f"{header}\n{body}" if body else header
        return json.dumps(data, indent=2)[:_PREVIEW_MAX]

    if data.get("error"):
        hint = data.get("hint", "")
        line = f"error: {data['error']}"
        if hint:
            line += f"\n{hint}"
        return line

    if tool_name == "get_thread":
        messages = data.get("messages") or []
        header = (
            f"channel: {data.get('channel_name') or data.get('channel_id', '?')} | "
            f"{len(messages)} msg(s) | reply_count={data.get('reply_count', '?')}"
        )
        body = _preview_messages(messages)
        return f"{header}\n{body}" if body else header

    if tool_name == "get_channel_history":
        messages = data if isinstance(data, list) else []
        header = f"{len(messages)} top-level message(s)"
        body = _preview_messages(messages)
        return f"{header}\n{body}" if body else header

    if tool_name in ("send_message", "send_message_thread"):
        if isinstance(data, dict):
            text = data.get("text") or data.get("message") or str(data)
            return f"posted ({len(text)} chars)"
        return "posted"

    if tool_name in ("get_messages_since_today", "search_slack_messages"):
        messages = data.get("messages") or []
        mode = f" | mode={data['mode']}" if data.get("mode") else ""
        header = (
            f"{data.get('count', len(messages))} message(s)"
            f"{mode} | query={data.get('query', '')}".rstrip(" | query=")
        )
        if data.get("channel_name"):
            header = f"#{data['channel_name']} | {header}"
        body = _preview_messages(messages)
        return f"{header}\n{body}" if body else header

    if tool_name == "search_messages":
        messages = data.get("messages") or []
        header = f"search: {data.get('count', len(messages))} hit(s)"
        body = _preview_messages(messages)
        return f"{header}\n{body}" if body else header

    if tool_name == "list_channels":
        channels = data if isinstance(data, list) else data.get("channels", [])
        if isinstance(channels, list):
            names = [f"#{c.get('name', c.get('id', '?'))}" for c in channels[:12] if isinstance(c, dict)]
            extra = f" (+{len(channels) - 12} more)" if len(channels) > 12 else ""
            return f"{len(channels)} channel(s): {', '.join(names)}{extra}"
        return str(data)[:_PREVIEW_MAX]

    return json.dumps(data, indent=2)[:_PREVIEW_MAX]


def format_tool_completion_message(tool_name: str, tool_result: str) -> str | None:
    """Build Slack debug text showing what the tool returned.

    Returns None for internal signals that must not appear in Slack (e.g. handoff).
    """
    raw = str(tool_result or "").strip()
    if tool_name == "handoff_to_tony" or raw.startswith("HANDOFF_READY:"):
        return None
    if tool_name == "create_schedule" and "Use create_project_schedule" in raw:
        return None
    if tool_name == "create_project_schedule":
        try:
            data = json.loads(raw)
            if data.get("status") == "created":
                name = data.get("name", "?")
                cron = data.get("cron", "?")
                tz = data.get("timezone", "")
                return f"✅ `{tool_name}`\nSchedule *{name}* — `{cron}` ({tz})".strip()
            if data.get("error"):
                return f"❌ `{tool_name}`\n{data['error']}"
        except json.JSONDecodeError:
            pass
    if not raw:
        return f"✅ `{tool_name}` completed _(empty result)_"

    preview = raw
    if tool_name in _SLACK_READ_TOOLS or raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                if tool_name == "list_channels" or (
                    parsed and isinstance(parsed[0], dict) and "name" in parsed[0] and "id" in parsed[0]
                ):
                    preview = _summarize_slack_json("list_channels", parsed)
                else:
                    preview = _summarize_slack_json("get_channel_history", parsed)
            elif tool_name in _SLACK_READ_TOOLS:
                preview = _summarize_slack_json(tool_name, parsed)
            elif isinstance(parsed, dict):
                preview = _summarize_slack_json(tool_name, parsed)
        except json.JSONDecodeError:
            preview = raw[:_PREVIEW_MAX]

    if len(preview) > _PREVIEW_MAX:
        preview = preview[:_PREVIEW_MAX] + "\n_(truncated)_"

    return f"✅ `{tool_name}`\n{preview}"
