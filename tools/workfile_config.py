"""Read/write helpers for the slack_eod_report project workfile."""

import json
import re
from pathlib import Path
from typing import Any

EOD_PROJECT = "slack_eod_report"
EOD_WORKFILE = Path("projects") / EOD_PROJECT / "workfile.md"
CONFIG_JSON_MARKER = "<!-- eod-config-json -->"

DEFAULT_CONFIG: dict[str, Any] = {
    "schedule": "0 17 * * 1-5",
    "timezone": "Australia/Melbourne",
    "channels": [],
    "deliver_to": {"type": "digest_channel", "channel_id": ""},
    "unanswered_threshold_hours": 2,
    "analysis_scope": {
        "unanswered_threads": True,
        "response_time_trends": True,
        "decision_tracking": True,
        "sentiment_flags": False,
        "custom": [],
    },
}


def workfile_exists() -> bool:
    return EOD_WORKFILE.exists()


def _extract_json_block(content: str) -> dict[str, Any]:
    pattern = rf"{re.escape(CONFIG_JSON_MARKER)}\s*```json\s*(.*?)\s*```"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(match.group(1))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return dict(DEFAULT_CONFIG)


def _replace_json_block(content: str, config: dict[str, Any]) -> str:
    block = f"{CONFIG_JSON_MARKER}\n```json\n{json.dumps(config, indent=2)}\n```"
    pattern = rf"{re.escape(CONFIG_JSON_MARKER)}\s*```json\s*.*?\s*```"
    if re.search(pattern, content, re.DOTALL):
        return re.sub(pattern, block, content, count=1, flags=re.DOTALL)
    if "## config" in content:
        return content.replace("## config", f"## config\n\n{block}", 1)
    return content + f"\n\n## config\n\n{block}\n"


def read_config() -> dict[str, Any]:
    if not EOD_WORKFILE.exists():
        return dict(DEFAULT_CONFIG)
    content = EOD_WORKFILE.read_text(encoding="utf-8")
    config = _extract_json_block(content)
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    if "analysis_scope" in config:
        merged["analysis_scope"] = {**DEFAULT_CONFIG["analysis_scope"], **config["analysis_scope"]}
    return merged


def write_config(config: dict[str, Any]) -> None:
    EOD_WORKFILE.parent.mkdir(parents=True, exist_ok=True)
    if EOD_WORKFILE.exists():
        content = EOD_WORKFILE.read_text(encoding="utf-8")
    else:
        content = ""
    updated = _replace_json_block(content, config)
    EOD_WORKFILE.write_text(updated, encoding="utf-8")


def append_channel(channel_id: str, channel_name: str) -> bool:
    """Append channel if not already present. Returns True if added."""
    if not workfile_exists():
        return False
    config = read_config()
    channels = config.get("channels") or []
    for ch in channels:
        if ch.get("id") == channel_id:
            return False
    channels.append({"id": channel_id, "name": channel_name})
    config["channels"] = channels
    write_config(config)
    return True


def append_report_history(entry: str) -> None:
    if not EOD_WORKFILE.exists():
        return
    content = EOD_WORKFILE.read_text(encoding="utf-8")
    marker = "## report_history"
    if marker not in content:
        content += f"\n\n{marker}\n{entry}\n"
    else:
        content = content.rstrip() + f"\n{entry}\n"
    EOD_WORKFILE.write_text(content, encoding="utf-8")


def append_trend_note(note: str) -> None:
    if not EOD_WORKFILE.exists():
        return
    content = EOD_WORKFILE.read_text(encoding="utf-8")
    marker = "## trends"
    if marker not in content:
        content += f"\n\n{marker}\n{note}\n"
    else:
        content = content.rstrip() + f"\n{note}\n"
    EOD_WORKFILE.write_text(content, encoding="utf-8")
