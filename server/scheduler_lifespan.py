"""Custom scheduler lifespan using SlackStreamingScheduleExecutor."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from agno.scheduler import SchedulePoller
from agno.utils.log import log_info

from server.slack_schedule_executor import SlackStreamingScheduleExecutor

if TYPE_CHECKING:
    from agno.os.app import AgentOS


@asynccontextmanager
async def stormrake_scheduler_lifespan(app, agent_os: "AgentOS"):
    """Start scheduler poller with in-process Slack streaming for scheduled runs."""
    if agent_os._scheduler_base_url is None:
        log_info(
            "scheduler_base_url not set, using default http://127.0.0.1:7777. "
            "If your server is running on a different port, set scheduler_base_url to match."
        )
    base_url = agent_os._scheduler_base_url or "http://127.0.0.1:7777"
    internal_token = agent_os._internal_service_token
    if internal_token is None:
        raise ValueError("internal_service_token must be set when scheduler is enabled")

    from server.agent_os import _build_jarvis, _build_tony

    executor = SlackStreamingScheduleExecutor(
        base_url=base_url,
        internal_service_token=internal_token,
        build_jarvis=_build_jarvis,
        build_tony=_build_tony,
    )
    poller = SchedulePoller(
        db=agent_os.db,
        executor=executor,
        poll_interval=agent_os._scheduler_poll_interval,
    )

    app.state.scheduler_executor = executor
    app.state.scheduler_poller = poller
    await poller.start()

    yield

    await poller.stop()
