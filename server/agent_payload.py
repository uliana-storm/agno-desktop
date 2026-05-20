"""Helpers for parsing AgentOS / scheduler run payloads."""

import json
from typing import Any


def merge_schedule_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Merge factory_input into top-level payload (mirrors server/agent_os._input_dict)."""
    if not payload:
        return {}

    data = dict(payload)
    factory_input = data.pop("factory_input", None)
    if isinstance(factory_input, str):
        try:
            factory_input = json.loads(factory_input)
        except json.JSONDecodeError:
            factory_input = {}
    if isinstance(factory_input, dict):
        data = {**data, **factory_input}
    return data
