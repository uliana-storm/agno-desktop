# Patch httpx before any imports to log all outbound HTTP requests
import httpx
import json
from datetime import datetime

def _log_request(url, kwargs):
    body = kwargs.get("json") or {}
    messages = body.get("messages", [])
    # rough token estimate: chars / 4
    total_chars = sum(len(m.get("content", "")) for m in messages if isinstance(m.get("content"), str))
    token_est = total_chars // 4
    
    # Identify the call type based on token size and content
    call_type = "unknown"
    if token_est < 100:
        call_type = "ping/telemetry"
    elif token_est < 500:
        call_type = "memory_update|compression|tool_ack"
    elif token_est < 1500:
        call_type = "session_summary|reasoning"
    else:
        call_type = "main_invoke"
    
    # Log first message preview for context identification
    first_msg_preview = ""
    if messages and len(messages) > 0:
        first_content = messages[0].get("content", "") if isinstance(messages[0], dict) else ""
        if isinstance(first_content, str):
            first_msg_preview = first_content[:80].replace("\n", " ")
    
    print(f"[{datetime.now().isoformat()}] POST {url} | type={call_type} | msgs={len(messages)} | ~tokens={token_est} | first_msg={first_msg_preview}...", flush=True)
    
    # Print breakdown of each message
    if messages:
        print(f"  [BREAKDOWN]:", flush=True)
        for i, msg in enumerate(messages):
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            chars = len(content) if isinstance(content, str) else sum(len(str(b)) for b in content)
            print(f"    [{i}] {role}: ~{chars//4} tokens", flush=True)
    
    # For large token counts, dump full content to diagnose bloat
    if token_est > 2000:
        print(f"  [FULL_DUMP] messages_content:", flush=True)
        for i, m in enumerate(messages[:5]):  # limit to first 5
            role = m.get("role", "?") if isinstance(m, dict) else "?"
            content = m.get("content", "") if isinstance(m, dict) else ""
            if isinstance(content, str):
                preview = content[:200].replace("\n", " ")
                print(f"    [{i}] {role}: {preview}... ({len(content)} chars)", flush=True)

# Sync
_orig_sync = httpx.Client.post
def _patched_sync(self, url, *args, **kwargs):
    _log_request(url, kwargs)
    return _orig_sync(self, url, *args, **kwargs)
httpx.Client.post = _patched_sync

# Async
_orig_async = httpx.AsyncClient.post
async def _patched_async(self, url, *args, **kwargs):
    _log_request(url, kwargs)
    return await _orig_async(self, url, *args, **kwargs)
httpx.AsyncClient.post = _patched_async

"""
AgentOS server for Jarvis and Tony with native scheduler support.

Run:
    .venv/bin/python server/agent_os.py

Requires local LLM endpoints (8081 Tony, 8082 Jarvis) for scheduled runs.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

load_dotenv(_root / ".env")

from agno.agent import Agent
from agno.agent.factory import AgentFactory
from agno.db.sqlite import SqliteDb
from agno.factory.utils import RequestContext
from agno.os import AgentOS

from agents.jarvis.agent import create_jarvis_agent
from agents.task_context import eod_run_instructions, slack_location_context, task_context
from agents.tony.agent import create_tony_agent
from server.paths import JARVIS_MEMORY_DB, TONY_MEMORY_DB, ensure_dirs
from server.scheduler_db import get_scheduler_db

ensure_dirs()

# Patch scheduler lifespan before AgentOS is constructed
import agno.os.app as agno_app
from server.scheduler_lifespan import stormrake_scheduler_lifespan

agno_app.scheduler_lifespan = stormrake_scheduler_lifespan

AGENT_OS_HOST = os.environ.get("AGENT_OS_HOST", "127.0.0.1")
AGENT_OS_PORT = int(os.environ.get("AGENT_OS_PORT", "7777"))


def _input_dict(ctx: RequestContext) -> dict:
    """Extract input dict from request context, merging factory_input if present."""
    if ctx.input is None:
        return {}

    if hasattr(ctx.input, "model_dump"):
        data = ctx.input.model_dump()
    elif isinstance(ctx.input, dict):
        data = dict(ctx.input)  # copy to avoid mutating original
    else:
        return {}

    # Option A: If factory_input is nested, merge it up (handles both flat and nested payloads)
    if "factory_input" in data:
        fi = data.pop("factory_input")  # remove the nested key
        if isinstance(fi, str):
            try:
                fi = json.loads(fi)
            except json.JSONDecodeError:
                fi = {}
        if isinstance(fi, dict):
            # Merge factory_input fields into top level (factory_input values take precedence)
            merged = {**data, **fi}
            return merged

    return data


def _slack_context(data: dict) -> str:
    channel = data.get("slack_channel")
    if not channel:
        return ""
    thread = data.get("thread_ts", "")
    return (
        f"\n\n## Scheduled Slack delivery\n"
        f"- slack_channel: {channel}\n"
        f"- thread_ts: {thread}\n"
        f"Your response will be streamed to Slack automatically. Do not call post_to_slack with your full answer."
    )


def _build_context(data: dict) -> str:
    parts = [data.get("additional_context", "")]
    parts.append(_slack_context(data))
    channel = data.get("slack_channel", "")
    thread = data.get("thread_ts", "")
    if channel:
        sid = data.get("session_id", "")
        parts.append(slack_location_context(channel, thread, session_id=sid or None))
    task = data.get("task", "")
    if task:
        parts.append(task_context(task, data.get("message", "")))
        parts.append(eod_run_instructions(task))
    return "".join(parts)


def _build_jarvis(ctx: RequestContext) -> Agent:
    from agno.utils.log import log_info

    data = _input_dict(ctx)
    print(f"[factory:_build_jarvis] ctx.session_id={ctx.session_id}, ctx.input={ctx.input}")
    print(f"[factory:_build_jarvis] extracted data: {data}")

    session_id = ctx.session_id or data.get("session_id") or "jarvis-scheduled"
    slack_channel = data.get("slack_channel")
    thread_ts = data.get("thread_ts")
    is_scheduled = data.get("is_scheduled", False)  # Extract scheduled flag from payload

    # Build additional_context with Slack delivery info
    additional = _build_context(data)
    log_info(f"[factory:jarvis] additional_context: {additional}")
    log_info(f"[factory:jarvis] session_id={session_id} type={'scheduled_run' if is_scheduled else 'regular'}")

    # Pass slack info and scheduled flag to agent for system prompt injection
    agent = create_jarvis_agent(
        session_id=session_id,
        additional_context=additional,
        slack_channel=slack_channel,
        thread_ts=thread_ts,
        is_scheduled=is_scheduled,
    )

    # Log the resolved model URL after agent creation
    if agent and agent.model:
        log_info(f"[factory:jarvis] resolved_model_url={agent.model.base_url} model_id={agent.model.id}")

    return agent


def _build_tony(ctx: RequestContext) -> Agent:
    from agno.utils.log import log_info

    data = _input_dict(ctx)
    session_id = ctx.session_id or data.get("session_id") or "tony-scheduled"
    additional = _build_context(data)
    project = data.get("project", "")
    workfile_path = data.get("workfile_path", "")
    if not workfile_path and project and project != "current":
        workfile_path = f"projects/{project}/workfile.md"

    slack_channel = data.get("slack_channel", "")
    thread_ts = data.get("thread_ts", "")
    if project and project != "current" and (not slack_channel or not thread_ts):
        from tools.create_project_schedule_tool import _load_handoff

        handoff = _load_handoff(project)
        if not slack_channel and handoff.get("slack_channel"):
            slack_channel = str(handoff["slack_channel"])
            log_info(f"[factory:tony] slack_channel from handoff.json: {slack_channel}")
        if not thread_ts and handoff.get("thread_ts"):
            thread_ts = str(handoff["thread_ts"])
            log_info(f"[factory:tony] thread_ts from handoff.json: {thread_ts}")

    log_info(f"[factory:tony] session_id={session_id} type=scheduled_run project={project}")
    log_info(f"[factory:tony] slack_channel={slack_channel}, thread_ts={thread_ts}")

    agent = create_tony_agent(
        session_id=session_id,
        additional_context=additional,
        workfile_path=workfile_path,
        slack_channel=slack_channel,
        thread_ts=thread_ts,
    )

    # Log the resolved model URL after agent creation
    if agent and agent.model:
        log_info(f"[factory:tony] resolved_model_url={agent.model.base_url} model_id={agent.model.id}")

    return agent


def create_agent_os() -> AgentOS:
    scheduler_db = get_scheduler_db()
    base_url = f"http://{AGENT_OS_HOST}:{AGENT_OS_PORT}"

    jarvis_factory = AgentFactory(
        id="jarvis",
        db=SqliteDb(db_file=str(JARVIS_MEMORY_DB)),
        factory=_build_jarvis,
        name="Jarvis",
        description="Intake coordinator — knowledge base, routing, scheduling",
    )
    tony_factory = AgentFactory(
        id="tony",
        db=SqliteDb(db_file=str(TONY_MEMORY_DB)),
        factory=_build_tony,
        name="Tony",
        description="Research specialist — project execution and scheduled runs",
    )

    return AgentOS(
        name="Stormrake AgentOS",
        agents=[jarvis_factory, tony_factory],
        db=scheduler_db,
        scheduler=True,
        scheduler_poll_interval=15,
        scheduler_base_url=base_url,
    )


agent_os = create_agent_os()
app = agent_os.get_app()


if __name__ == "__main__":
    print(f"Starting AgentOS on http://{AGENT_OS_HOST}:{AGENT_OS_PORT} (scheduler enabled)")
    agent_os.serve(
        app="server.agent_os:app",
        host=AGENT_OS_HOST,
        port=AGENT_OS_PORT,
        reload=False,
    )
