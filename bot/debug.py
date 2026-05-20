"""Debug logging toggles for the Slack bot."""

import os


def agent_debug_enabled() -> bool:
    return os.environ.get("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes")
