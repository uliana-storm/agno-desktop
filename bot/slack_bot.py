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
Slack bot server for the Jarvis + Tony multi-agent system.
Uses Socket Mode — no public URL or ngrok needed.
The bot responds in the same DM or channel thread it was mentioned in,
and uploads any files the agent creates to that conversation.

Architecture:
- Jarvis: Intake agent (FileTools only, reads knowledge/)
- Tony: Research agent (full toolkit, writes to output/)
- Router: Pre-routing logic for agent selection
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from slack_bolt import App

# Add parent directory to path for imports
_parent_dir = Path(__file__).parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

from slack_bolt.adapter.socket_mode import SocketModeHandler

from agents.jarvis.agent import create_jarvis_agent
from agents.tony.agent import create_tony_agent
from agents.task_context import (
    handoff_schedule_context,
    looks_like_scheduling_request,
    scheduling_datetime_context,
    slack_location_context,
    tony_slack_instructions,
)
from bot.agent_runner import post_timing_message, run_agent, upload_files
from bot.router import route, register_thread
from server.paths import OUTPUT_DIR, ensure_dirs
from tools.workfile_config import append_channel, workfile_exists


# ---------------------------------------------------------------------------
# Config — set these in your environment
# ---------------------------------------------------------------------------

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

ensure_dirs()

app = App(
    token=SLACK_BOT_TOKEN,
    request_verification_enabled=False,
)

_bot_user_id: str | None = None


def get_bot_user_id(client) -> str:
    global _bot_user_id
    if _bot_user_id is None:
        _bot_user_id = client.auth_test()["user_id"]
    return _bot_user_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_mentions(text: str) -> str:
    """Remove <@USERID> mentions from message text."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def prepend_slack_location(
    context: str,
    channel: str,
    thread_ts: str,
    session_id: str,
) -> str:
    """Put ## Slack location at the top of additional_context."""
    if not channel:
        return context
    loc = slack_location_context(channel, thread_ts, session_id=session_id).strip()
    if context:
        return loc + "\n\n" + context
    return loc


def build_tony_handoff_prompt(project_name: str, handoff_data: dict, user_prompt: str) -> str:
    task = handoff_data.get("task", "")
    if task == "eod_report_init":
        return (
            "Task: eod_report_init. Read handoff.json and execute the EOD Report Project init steps in "
            "your prompt: list_channels, create projects/slack_eod_report/workfile.md with config JSON, "
            "create_project_schedule for weekday 5pm Australia/Melbourne; confirm in one streamed line only."
        )
    if task == "eod_report_adjust":
        return (
            f"Task: eod_report_adjust. User request: {user_prompt}. Update "
            f"projects/slack_eod_report/workfile.md config per their request. Confirm the change."
        )
    return (
        f"New project handoff received. Read handoff.json, create or update "
        f"{project_name}/workfile.md via save_file(scope=projects, path=...) — no projects/ prefix in path. "
        f"If scope is ongoing and timing was specified, call create_project_schedule with "
        f"slack_channel and thread_ts from ## Slack location. "
        f"Confirm in one short streamed message only — do not call post_to_slack."
    )


def format_project_options(matches: list[dict]) -> str:
    """Format matched projects for Jarvis context."""
    lines = ["Matched projects from registry:"]
    for m in matches:
        lines.append(f"- {m['name']} ({m.get('status', 'unknown')}): {m.get('summary', '')}")
    return "\n".join(lines)


def handle_message(body: dict, client, say) -> None:
    """Shared handler for DMs and @mentions with handoff interception."""
    t0 = time.time()
    print(f"[TIMER] message received: {t0:.3f}", flush=True)

    event = body.get("event", {})
    raw_text = event.get("text", "")
    channel = event.get("channel")
    channel_type = event.get("channel_type", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    user_id = event.get("user", "unknown")
    prompt = strip_mentions(raw_text)

    if not prompt:
        say(text="What would you like me to help you with?", thread_ts=thread_ts)
        return

    if len(prompt) < 10:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ Please provide a more detailed query (at least 10 characters).",
        )
        return

    routing = route(prompt, thread_ts)
    print(f"[TIMER] after routing: {time.time()-t0:.3f}s", flush=True)
    target_agent = routing["agent"]
    mode = routing["mode"]
    project = routing.get("project")
    matches = routing.get("matches", [])

    print(f"[router] Agent: {target_agent}, Mode: {mode}, Project: {project}")

    if target_agent == "tony":
        tony_session_id = f"tony-{user_id}-{thread_ts}"
        ack_text = "Continuing with the project... 🔄" if mode == "continue" else "On it — this may take several minutes ⏳"

        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=ack_text,
        )

        def process_tony():
            start = perf_counter()
            try:
                workfile_path = project.get("workfile_path", "") if project else ""
                additional_context = ""
                if project and project.get("handoff"):
                    additional_context = json.dumps(project["handoff"], indent=2)
                elif channel:
                    additional_context = ""
                additional_context = prepend_slack_location(
                    additional_context, channel, thread_ts, tony_session_id
                )
                if project and project.get("handoff"):
                    additional_context += handoff_schedule_context(project["handoff"])
                additional_context += tony_slack_instructions()

                tony = create_tony_agent(
                    session_id=tony_session_id,
                    additional_context=additional_context,
                    workfile_path=workfile_path,
                    slack_channel=channel,
                    thread_ts=thread_ts,
                )
                response, new_files, _ = run_agent(
                    tony, prompt, client, channel, thread_ts, tony_session_id, t0, stream_to_slack=True
                )
                print(f"\n[Tony] FULL RESPONSE:\n{response}\n")
                elapsed = perf_counter() - start
                post_timing_message(client, channel, thread_ts, elapsed)

                if new_files:
                    upload_files(client, channel, thread_ts, new_files)

            except Exception as e:
                import traceback
                print(f"[tony error] {e}")
                print(traceback.format_exc())
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"❌ Error: {e}",
                )

        threading.Thread(target=process_tony, daemon=True).start()
        return

    jarvis_session_id = (
        f"jarvis-{user_id}" if channel_type == "im" else f"jarvis-{user_id}-{thread_ts}"
    )

    jarvis_context = ""
    if mode == "project_select" and matches:
        jarvis_context = format_project_options(matches)
        ack_text = "I found some matching projects. Let me help you select one... 🔍"
    else:
        ack_text = "Looking into this for you... 🔍"

    if channel:
        jarvis_context = prepend_slack_location(
            jarvis_context, channel, thread_ts, jarvis_session_id
        )
        if looks_like_scheduling_request(prompt):
            jarvis_context += scheduling_datetime_context(prompt)

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=ack_text,
    )

    def process_jarvis():
        start = perf_counter()
        try:
            jarvis = create_jarvis_agent(
                session_id=jarvis_session_id,
                additional_context=jarvis_context,
                slack_channel=channel,
                thread_ts=thread_ts,
                is_scheduled=False,
            )
            response, new_files, handoff_project = run_agent(
                jarvis, prompt, client, channel, thread_ts, jarvis_session_id, t0, stream_to_slack=True
            )
            print(f"\n[Jarvis] FULL RESPONSE:\n{response}\n")
            elapsed = perf_counter() - start

            if handoff_project:
                project_name = handoff_project

                register_thread(thread_ts, project_name)

                tony_session_id = f"tony-{user_id}-{thread_ts}"
                handoff_path = f"projects/{project_name}/handoff.json"
                handoff_data = {}
                handoff_context = ""
                if os.path.exists(handoff_path):
                    with open(handoff_path) as f:
                        handoff_context = f.read()
                        handoff_data = json.loads(handoff_context)

                handoff_context = prepend_slack_location(
                    handoff_context, channel, thread_ts, tony_session_id
                )
                handoff_context += handoff_schedule_context(handoff_data)
                handoff_context += tony_slack_instructions()

                tony = create_tony_agent(
                    session_id=tony_session_id,
                    additional_context=handoff_context,
                    workfile_path=f"projects/{project_name}/workfile.md",
                    slack_channel=channel,
                    thread_ts=thread_ts,
                )
                tony_prompt = build_tony_handoff_prompt(project_name, handoff_data, prompt)

                tony_response, tony_files, _ = run_agent(
                    tony, tony_prompt, client, channel, thread_ts, tony_session_id, t0, stream_to_slack=True
                )
                print(f"[process_jarvis] Tony response length: {len(tony_response)} chars", flush=True)
                print(f"[process_jarvis] Tony files found: {tony_files}", flush=True)

                post_timing_message(client, channel, thread_ts, perf_counter() - start)

                if tony_files:
                    upload_files(client, channel, thread_ts, tony_files)
                return

            post_timing_message(client, channel, thread_ts, elapsed)

            if new_files:
                upload_files(client, channel, thread_ts, new_files)

        except Exception as e:
            import traceback
            print(f"[jarvis error] {e}")
            print(traceback.format_exc())
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"❌ Error: {e}",
            )

    threading.Thread(target=process_jarvis, daemon=True).start()


@app.event("app_mention")
def handle_mention(body, client, say):
    handle_message(body, client, say)


@app.event("message")
def handle_dm(body, client, say):
    event = body.get("event", {})
    if event.get("channel_type") == "im" and not event.get("bot_id"):
        handle_message(body, client, say)


@app.event("member_joined_channel")
def handle_member_joined(body, client, logger):
    event = body.get("event", {})
    try:
        if event.get("user") != get_bot_user_id(client):
            return
    except Exception as e:
        logger.warning(f"[eod] could not verify bot user: {e}")
        return

    if not workfile_exists():
        return

    channel_id = event.get("channel", "")
    channel_name = channel_id
    try:
        info = client.conversations_info(channel=channel_id)
        channel_name = info.get("channel", {}).get("name", channel_id)
    except Exception as e:
        logger.warning(f"[eod] could not resolve channel name: {e}")

    if append_channel(channel_id, channel_name):
        logger.info(f"[eod] auto-enrolled channel #{channel_name} ({channel_id})")
        print(f"[eod] auto-enrolled channel #{channel_name} ({channel_id})", flush=True)


if __name__ == "__main__":
    print("Starting Jarvis + Tony Slack bot...")
    print(f"Output dir: {OUTPUT_DIR.resolve()}")
    print("Jarvis: port 8082 (Qwen 7B instruct)")
    print("Tony: port 8081 (Qwopus 35B A3)")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
