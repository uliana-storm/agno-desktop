"""
Slack bot server for the Jarvis + Tony multi-agent system.
Uses Socket Mode — no public URL or ngrok needed.

Architecture:
- Jarvis: Intake agent (FileTools only, reads knowledge/)
- Tony:   Research agent (full toolkit, writes to output/)
- Router: Pre-routing logic — escape hatch and project close supported
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

# Add parent directory to path for imports
_parent_dir = Path(__file__).parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

# ---------------------------------------------------------------------------
# Optional httpx request logging — only when AGENT_DEBUG=1
# ---------------------------------------------------------------------------

if os.environ.get("AGENT_DEBUG") == "1":
    import httpx
    from datetime import datetime

    def _log_request(url: str, kwargs: dict) -> None:
        body = kwargs.get("json") or {}
        messages = body.get("messages", [])
        total_chars = sum(
            len(m.get("content", "")) for m in messages
            if isinstance(m.get("content"), str)
        )
        token_est = total_chars // 4
        call_type = (
            "ping/telemetry"               if token_est < 100   else
            "memory_update|compression"    if token_est < 500   else
            "session_summary|reasoning"    if token_est < 1500  else
            "main_invoke"
        )
        first_preview = ""
        if messages:
            first_content = messages[0].get("content", "") if isinstance(messages[0], dict) else ""
            if isinstance(first_content, str):
                first_preview = first_content[:80].replace("\n", " ")
        print(
            f"[{datetime.now().isoformat()}] POST {url} | "
            f"type={call_type} | msgs={len(messages)} | ~tokens={token_est} | "
            f"first_msg={first_preview}...",
            flush=True,
        )
        if messages:
            print("  [BREAKDOWN]:", flush=True)
            for i, msg in enumerate(messages):
                role    = msg.get("role", "?") if isinstance(msg, dict) else "?"
                content = msg.get("content", "") if isinstance(msg, dict) else ""
                chars   = len(content) if isinstance(content, str) else sum(len(str(b)) for b in content)
                print(f"    [{i}] {role}: ~{chars//4} tokens", flush=True)

    _orig_sync = httpx.Client.post
    def _patched_sync(self, url, *args, **kwargs):
        _log_request(url, kwargs)
        return _orig_sync(self, url, *args, **kwargs)
    httpx.Client.post = _patched_sync

    _orig_async = httpx.AsyncClient.post
    async def _patched_async(self, url, *args, **kwargs):
        _log_request(url, kwargs)
        return await _orig_async(self, url, *args, **kwargs)
    httpx.AsyncClient.post = _patched_async

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agents.jarvis.agent import create_jarvis_agent
from agents.tony.agent import create_tony_agent
from agents.task_context import (
    looks_like_scheduling_request,
    scheduling_datetime_context,
    slack_location_context,
    tony_slack_instructions,
)
from bot.agent_runner import post_timing_message, run_agent, upload_files
from bot.router import register_thread, route, unregister_thread
from server.paths import OUTPUT_DIR, ensure_dirs

# ---------------------------------------------------------------------------
# Config
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
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def prepend_slack_location(
    context: str,
    channel: str,
    thread_ts: str,
    session_id: str,
) -> str:
    if not channel:
        return context
    loc = slack_location_context(channel, thread_ts, session_id=session_id).strip()
    return loc + "\n\n" + context if context else loc


def build_tony_handoff_prompt(project_name: str, handoff_data: dict, user_prompt: str) -> str:
    """Build Tony's first prompt after a Jarvis handoff."""
    return (
        f"New project handoff received for '{project_name}'. "
        f"Read handoff.json, then create or update "
        f"{project_name}/workfile.md via save_file(scope=projects, path=...) — "
        f"no projects/ prefix in path. "
        f"If scope is ongoing and timing was specified, call the appropriate schedule tool "
        f"with slack_channel and thread_ts from ## Slack location. "
        f"Confirm in one short streamed message only."
    )


def format_project_options(matches: list[dict]) -> str:
    lines = ["Matched projects from registry:"]
    for m in matches:
        lines.append(f"- {m['name']} ({m.get('status', 'unknown')}): {m.get('summary', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------

def handle_message(body: dict, client, say) -> None:
    t0 = time.time()
    print(f"[TIMER] message received: {t0:.3f}", flush=True)

    event        = body.get("event", {})
    raw_text     = event.get("text", "")
    channel      = event.get("channel")
    channel_type = event.get("channel_type", "")
    thread_ts    = event.get("thread_ts") or event.get("ts")
    user_id      = event.get("user", "unknown")
    prompt       = strip_mentions(raw_text)

    if not prompt:
        say(text="What would you like me to help you with?", thread_ts=thread_ts)
        return

    routing      = route(prompt, thread_ts)
    target_agent = routing["agent"]
    mode         = routing["mode"]
    project      = routing.get("project")
    matches      = routing.get("matches", [])

    print(f"[router] agent={target_agent} mode={mode} project={project}", flush=True)

    # ------------------------------------------------------------------
    # Handle new router modes before dispatching
    # ------------------------------------------------------------------

    if mode == "project_closed":
        closed = routing.get("closed_project", "the project")
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"Project *{closed}* closed. I'm back — what can I help you with?",
        )
        # Fall through to Jarvis for any follow-up in the same message

    elif mode == "escape":
        # Silent — Jarvis handles it naturally, no special message needed
        pass

    # ------------------------------------------------------------------
    # Tony
    # ------------------------------------------------------------------

    if target_agent == "tony":
        tony_session_id = f"tony-{user_id}-{thread_ts}"
        ack_text = (
            "Continuing with the project... 🔄"
            if mode == "continue"
            else "On it — this may take several minutes ⏳"
        )
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=ack_text)

        def process_tony():
            start = perf_counter()
            try:
                workfile_path      = project.get("workfile_path", "") if project else ""
                additional_context = ""
                if project and project.get("handoff"):
                    additional_context = json.dumps(project["handoff"], indent=2)
                additional_context = prepend_slack_location(
                    additional_context, channel, thread_ts, tony_session_id
                )
                additional_context += tony_slack_instructions()

                tony = create_tony_agent(
                    session_id=tony_session_id,
                    additional_context=additional_context,
                    workfile_path=workfile_path,
                    slack_channel=channel,
                    thread_ts=thread_ts,
                )
                response, new_files, _ = run_agent(
                    tony, prompt, client, channel, thread_ts,
                    tony_session_id, t0, stream_to_slack=True,
                )
                print(f"\n[Tony] response length: {len(response)} chars", flush=True)
                post_timing_message(client, channel, thread_ts, perf_counter() - start)
                if new_files:
                    upload_files(client, channel, thread_ts, new_files)

            except Exception as e:
                import traceback
                print(f"[tony error] {e}\n{traceback.format_exc()}", flush=True)
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts, text=f"❌ Error: {e}"
                )

        threading.Thread(target=process_tony, daemon=True).start()
        return

    # ------------------------------------------------------------------
    # Jarvis
    # ------------------------------------------------------------------

    jarvis_session_id = (
        f"jarvis-{user_id}"
        if channel_type == "im"
        else f"jarvis-{user_id}-{thread_ts}"
    )

    if mode == "project_select" and matches:
        jarvis_context = format_project_options(matches)
        ack_text = "I found some matching projects. Let me help you select one... 🔍"
    else:
        jarvis_context = ""
        ack_text = "Looking into this for you... 🔍"

    if channel:
        jarvis_context = prepend_slack_location(
            jarvis_context, channel, thread_ts, jarvis_session_id
        )
        if looks_like_scheduling_request(prompt):
            jarvis_context += scheduling_datetime_context(prompt)

    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=ack_text)

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
                jarvis, prompt, client, channel, thread_ts,
                jarvis_session_id, t0, stream_to_slack=True,
            )
            print(f"\n[Jarvis] response length: {len(response)} chars", flush=True)

            # ----------------------------------------------------------
            # Handoff → Tony
            # ----------------------------------------------------------
            if handoff_project:
                register_thread(thread_ts, handoff_project)

                tony_session_id = f"tony-{user_id}-{thread_ts}"
                handoff_path    = f"projects/{handoff_project}/handoff.json"
                handoff_data    = {}
                handoff_context = ""

                if os.path.exists(handoff_path):
                    with open(handoff_path) as f:
                        handoff_context = f.read()
                        handoff_data    = json.loads(handoff_context)

                handoff_context = prepend_slack_location(
                    handoff_context, channel, thread_ts, tony_session_id
                )
                handoff_context += tony_slack_instructions()

                tony = create_tony_agent(
                    session_id=tony_session_id,
                    additional_context=handoff_context,
                    workfile_path=f"projects/{handoff_project}/workfile.md",
                    slack_channel=channel,
                    thread_ts=thread_ts,
                )
                tony_prompt = build_tony_handoff_prompt(handoff_project, handoff_data, prompt)
                tony_response, tony_files, _ = run_agent(
                    tony, tony_prompt, client, channel, thread_ts,
                    tony_session_id, t0, stream_to_slack=True,
                )
                print(
                    f"[handoff] Tony response: {len(tony_response)} chars, "
                    f"files: {tony_files}",
                    flush=True,
                )
                post_timing_message(client, channel, thread_ts, perf_counter() - start)
                if tony_files:
                    upload_files(client, channel, thread_ts, tony_files)
                return

            post_timing_message(client, channel, thread_ts, perf_counter() - start)
            if new_files:
                upload_files(client, channel, thread_ts, new_files)

        except Exception as e:
            import traceback
            print(f"[jarvis error] {e}\n{traceback.format_exc()}", flush=True)
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text=f"❌ Error: {e}"
            )

    threading.Thread(target=process_jarvis, daemon=True).start()


# ---------------------------------------------------------------------------
# Slack event handlers
# ---------------------------------------------------------------------------

@app.event("app_mention")
def handle_mention(body, client, say):
    handle_message(body, client, say)


@app.event("message")
def handle_dm(body, client, say):
    event = body.get("event", {})
    if event.get("channel_type") == "im" and not event.get("bot_id"):
        handle_message(body, client, say)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting Jarvis + Tony Slack bot...")
    print(f"Output dir: {OUTPUT_DIR.resolve()}")
    print("Jarvis: port 8082 (Gemma 27B)")
    print("Tony:   port 8081 (Qwopus 35B)")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()