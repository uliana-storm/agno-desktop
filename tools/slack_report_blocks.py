"""Build Slack Block Kit payloads for EOD reports."""

from typing import Any


def _section_lines(lines: list[str]) -> str:
    return "\n".join(f"• {line}" for line in lines if line.strip())


def build_eod_blocks(
    date: str,
    done_items: list[str],
    unanswered: list[dict[str, Any]],
    in_progress: list[str],
    trend: str = "",
) -> list[dict[str, Any]]:
    """Build Block Kit blocks. Omits empty sections."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Daily Report — {date}", "emoji": True},
        }
    ]

    if done_items:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*✅ What got done*\n{_section_lines(done_items)}",
                },
            }
        )
        blocks.append({"type": "divider"})

    if unanswered:
        lines = []
        for item in unanswered:
            user = item.get("user", "someone")
            message = item.get("message", "")[:120]
            hours = item.get("hours", "?")
            link = item.get("link", "")
            line = f"@{user} asked: \"{message}\" — {hours}h ago"
            if link:
                line += f" (<{link}|view>)"
            lines.append(line)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*❌ Unanswered ({len(unanswered)})*\n" + "\n".join(f"• {l}" for l in lines),
                },
            }
        )
        blocks.append({"type": "divider"})

    if in_progress:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔄 Still in progress*\n{_section_lines(in_progress)}",
                },
            }
        )
        blocks.append({"type": "divider"})

    if trend.strip():
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*📈 Trend*\n{trend.strip()}"},
            }
        )
        blocks.append({"type": "divider"})

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Adjust scope: talk to me in this channel.",
                }
            ],
        }
    )
    return blocks
