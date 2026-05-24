"""Schedule executor that streams agent runs to Slack in-process."""

import asyncio
import os
import time
from time import perf_counter
from typing import Any, Callable, Dict

from agno.agent import Agent
from agno.db.schemas.scheduler import Schedule
from agno.factory.utils import RequestContext
from agno.scheduler.executor import ScheduleExecutor, _RUN_ENDPOINT_RE
from agno.utils.log import log_info
from slack_sdk import WebClient

from bot.agent_runner import post_timing_message, run_agent, upload_files
from server.agent_payload import merge_schedule_payload
from server.scheduler_db import get_scheduler_db

_RUN_AGENTS = {"jarvis", "tony"}


class SlackStreamingScheduleExecutor(ScheduleExecutor):
    """Execute scheduled agent runs with Slack streaming when delivery context is present."""

    def __init__(
        self,
        *,
        build_jarvis: Callable[[RequestContext], Agent],
        build_tony: Callable[[RequestContext], Agent],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.build_jarvis = build_jarvis
        self.build_tony = build_tony

    async def _call_endpoint(self, schedule: Schedule | Dict[str, Any]) -> Dict[str, Any]:
        sched = Schedule.from_dict(schedule) if isinstance(schedule, dict) else schedule
        payload = sched.payload or {}
        data = merge_schedule_payload(payload)

        endpoint = sched.endpoint or ""
        method = (sched.method or "POST").upper()
        match = _RUN_ENDPOINT_RE.match(endpoint)

        if (
            match
            and method == "POST"
            and data.get("slack_channel")
            and data.get("thread_ts")
        ):
            agent_id = match.group(2)
            if agent_id in _RUN_AGENTS:
                log_info(
                    f"[slack-executor] In-process Slack stream for {agent_id} "
                    f"schedule={sched.id} channel={data.get('slack_channel')}"
                )
                return await asyncio.to_thread(
                    self._run_with_slack_streaming, agent_id, data, sched.id
                )

        return await super()._call_endpoint(schedule)

    def _run_with_slack_streaming(
        self, agent_id: str, data: dict[str, Any], schedule_id: str | None = None
    ) -> Dict[str, Any]:
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not token:
            return {
                "status": "failed",
                "status_code": None,
                "error": "SLACK_BOT_TOKEN is not set",
                "run_id": None,
                "session_id": data.get("session_id"),
                "input": None,
                "output": None,
                "requirements": None,
            }

        message = data.get("message", "")
        channel = data["slack_channel"]
        thread_ts = data["thread_ts"]
        session_id = data.get("session_id") or f"{agent_id}-scheduled"

        run_data = dict(data)
        run_data["is_scheduled"] = True
        ctx = RequestContext(session_id=session_id, input=run_data)

        try:
            if agent_id == "jarvis":
                agent = self.build_jarvis(ctx)
            else:
                agent = self.build_tony(ctx)
        except Exception as exc:
            return {
                "status": "failed",
                "status_code": None,
                "error": str(exc),
                "run_id": None,
                "session_id": session_id,
                "input": {"message": message},
                "output": None,
                "requirements": None,
            }

        client = WebClient(token=token)
        start = perf_counter()
        t0 = time.time()

        try:
            response, new_files, _, _ = run_agent(
                agent,
                message,
                client,
                channel,
                thread_ts,
                session_id,
                t0,
                stream_to_slack=True,
            )
            post_timing_message(client, channel, thread_ts, perf_counter() - start)
            if new_files:
                upload_files(client, channel, thread_ts, new_files)
        except Exception as exc:
            return {
                "status": "failed",
                "status_code": None,
                "error": str(exc),
                "run_id": None,
                "session_id": session_id,
                "input": {"message": message},
                "output": None,
                "requirements": None,
            }

        if data.get("disable_schedule_after_run") and schedule_id:
            try:
                from agno.scheduler.manager import ScheduleManager

                ScheduleManager(db=get_scheduler_db()).disable(schedule_id)
                log_info(f"[slack-executor] Disabled one-shot schedule {schedule_id}")
            except Exception as exc:
                log_info(f"[slack-executor] Could not disable schedule {schedule_id}: {exc}")

        return {
            "status": "success",
            "status_code": 200,
            "error": None,
            "run_id": None,
            "session_id": session_id,
            "input": {"message": message},
            "output": {"content": response, "content_type": "str"},
            "requirements": None,
        }
