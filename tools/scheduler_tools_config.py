"""AgentOS scheduler toolkits — block raw create_schedule; use wrappers for creates."""

import json
from typing import Any

from agno.tools.scheduler import SchedulerTools

from server.scheduler_db import get_scheduler_db

_CREATE_BLOCKED_MSG = json.dumps(
    {
        "error": "create_schedule is disabled. "
        "Jarvis: use schedule_reminder_in_minutes for relative reminders. "
        "Tony: use create_project_schedule for recurring project cron jobs.",
    }
)


class BlockedCreateSchedulerTools(SchedulerTools):
    """SchedulerTools without LLM-driven create_schedule."""

    def create_schedule(self, *args: Any, **kwargs: Any) -> str:
        return _CREATE_BLOCKED_MSG

    async def acreate_schedule(self, *args: Any, **kwargs: Any) -> str:
        return _CREATE_BLOCKED_MSG


def jarvis_scheduler_tools() -> BlockedCreateSchedulerTools:
    return BlockedCreateSchedulerTools(
        db=get_scheduler_db(),
        default_endpoint="/agents/jarvis/runs",
        default_timezone="Australia/Melbourne",
        instructions=(
            "List, get, enable, disable, or delete schedules. "
            "For new reminders use schedule_reminder_in_minutes — not create_schedule."
        ),
    )


class TonySchedulerTools(BlockedCreateSchedulerTools):
    """Tony scheduler toolkit — create via create_project_schedule only."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.instructions = (
            "Manage existing schedules with list_schedules, get_schedule, enable_schedule, "
            "disable_schedule, delete_schedule, and get_schedule_runs. "
            "To CREATE or UPDATE a recurring project schedule, call create_project_schedule — "
            "not create_schedule."
        )


def tony_scheduler_tools() -> TonySchedulerTools:
    return TonySchedulerTools(
        db=get_scheduler_db(),
        default_endpoint="/agents/tony/runs",
        default_timezone="Australia/Melbourne",
    )
