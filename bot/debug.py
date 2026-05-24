"""Debug flag helper for the Slack bot."""

import os


def agent_debug_enabled() -> bool:
    """Return True if AGENT_DEBUG=1 is set in the environment."""
    return os.environ.get("AGENT_DEBUG") == "1"