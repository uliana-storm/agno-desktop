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
from typing import Literal

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
    melbourne_datetime_context,
    scheduling_datetime_context,
    slack_location_context,
    tony_slack_instructions,
)
from bot.agent_runner import cancel_key_for, cancel_run, post_timing_message, run_agent, upload_files
from bot.jarvis_ack_phrases import random_jarvis_ack
from bot.router import register_thread, route
from server.paths import OUTPUT_DIR, PROJECTS_DIR, ensure_dirs, similarity_sentinel_path

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
# Pending handoff state (similarity disambiguation)
# ---------------------------------------------------------------------------

_pending_handoffs: dict[str, dict] = {}
_pending_lock = threading.Lock()
_PENDING_TTL_SECONDS = 3600

_NEW_PROJECT_RE = re.compile(
    r"(?i)\b(new project|start new|create new|proceed with new)\b"
)


def _name_in_prompt(name: str, prompt_lower: str) -> bool:
    return name in prompt_lower or name.replace("-", " ") in prompt_lower


def _store_pending(thread_ts: str, pending: dict) -> None:
    pending["stored_at"] = time.time()
    with _pending_lock:
        _pending_handoffs[thread_ts] = pending


def _get_pending(thread_ts: str) -> dict | None:
    with _pending_lock:
        pending = _pending_handoffs.get(thread_ts)
        if not pending:
            return None
        if time.time() - pending.get("stored_at", 0) > _PENDING_TTL_SECONDS:
            del _pending_handoffs[thread_ts]
            return None
        return pending


def _pop_pending(thread_ts: str) -> dict | None:
    with _pending_lock:
        return _pending_handoffs.pop(thread_ts, None)


def _parse_pending_choice(prompt: str, pending: dict) -> str | None:
    """Return chosen project_name if prompt is an explicit disambiguation reply."""
    prompt_lower = prompt.lower()
    if _NEW_PROJECT_RE.search(prompt_lower):
        return pending["proposed_name"]
    for match in sorted(pending["matches"], key=lambda m: len(m["name"]), reverse=True):
        if _name_in_prompt(match["name"], prompt_lower):
            return match["name"]
    if _name_in_prompt(pending["proposed_name"], prompt_lower):
        return pending["proposed_name"]
    return None


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


def build_tony_handoff_prompt(project_name: str) -> str:
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


def _format_project_list(
    matches: list[dict],
    *,
    header: str,
    slack: bool = False,
    proposed_name: str | None = None,
    footer: str | None = None,
) -> str:
    lines = [header]
    for m in matches:
        status = m.get("status", "unknown")
        summary = m.get("summary", "")
        if slack:
            suffix = f" — {summary[:80]}" if summary else ""
            status_part = f" ({status})" if status else ""
            lines.append(f"• *{m['name']}*{status_part}{suffix}")
        else:
            lines.append(f"- {m['name']} ({status}): {summary}")
    if proposed_name is not None:
        lines.extend(["", f"Proposed new project: *{proposed_name}*"])
    if footer:
        lines.extend(["", footer])
    return "\n".join(lines)


def _post_similarity_disambiguation(
    client,
    channel: str,
    thread_ts: str,
    proposed_name: str,
    matches: list[dict],
) -> None:
    text = _format_project_list(
        matches,
        header="Found similar active projects:",
        slack=True,
        proposed_name=proposed_name,
        footer=(
            "Reply with an existing project name to continue it, "
            "or *new project* to proceed with the new one."
        ),
    )
    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)


def _dispatch_tony(
    *,
    mode: Literal["handoff", "continue"],
    project_name: str,
    tony_prompt: str,
    user_id: str,
    channel: str,
    thread_ts: str,
    client,
    t0: float,
    run_key: str,
    handoff_data: dict | None = None,
    workfile_path: str = "",
    register: bool = False,
) -> None:
    if register:
        register_thread(thread_ts, project_name)

    tony_session_id = f"tony-{user_id}-{thread_ts}"
    additional_context = json.dumps(handoff_data, indent=2) if handoff_data else ""
    additional_context = prepend_slack_location(
        additional_context, channel, thread_ts, tony_session_id
    )
    additional_context += tony_slack_instructions()

    if not workfile_path:
        workfile_path = str(PROJECTS_DIR / project_name / "workfile.md")

    tony = create_tony_agent(
        session_id=tony_session_id,
        additional_context=additional_context,
        workfile_path=workfile_path,
        slack_channel=channel,
        thread_ts=thread_ts,
    )

    log_label = "handoff" if mode == "handoff" else "tony"

    def run_tony():
        start = perf_counter()
        try:
            tony_response, tony_files, _, was_cancelled = run_agent(
                tony, tony_prompt, client, channel, thread_ts,
                tony_session_id, t0, stream_to_slack=True,
                cancel_key=run_key,
            )
            if was_cancelled:
                return
            print(
                f"[{log_label}] Tony response: {len(tony_response)} chars, files: {tony_files}",
                flush=True,
            )
            post_timing_message(client, channel, thread_ts, perf_counter() - start)
            if tony_files:
                upload_files(client, channel, thread_ts, tony_files)
        except Exception as e:
            import traceback
            print(f"[{log_label} error] {e}\n{traceback.format_exc()}", flush=True)
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"❌ Error: {e}")

    threading.Thread(target=run_tony, daemon=True).start()


def _dispatch_tony_from_pending(
    pending: dict,
    project_name: str,
    user_id: str,
    channel: str,
    thread_ts: str,
    client,
    t0: float,
    run_key: str,
) -> None:
    params = dict(pending["brief_params"])
    params["project_name"] = project_name

    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    with open(project_dir / "handoff.json", "w") as f:
        json.dump(params, f, indent=2)

    _dispatch_tony(
        mode="handoff",
        project_name=project_name,
        tony_prompt=build_tony_handoff_prompt(project_name),
        user_id=user_id,
        channel=channel,
        thread_ts=thread_ts,
        client=client,
        t0=t0,
        run_key=run_key,
        handoff_data=params,
        register=True,
    )


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------

_STOP_RE = re.compile(r"(?i)^(stop|cancel|abort|halt|quit|end)\.?$")


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

    run_key = cancel_key_for(channel, thread_ts, channel_type)
    if _STOP_RE.match(prompt):
        _pop_pending(thread_ts)
        if cancel_run(run_key):
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="⏹️ Stopping the current run...",
            )
        else:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="No active run to cancel in this conversation.",
            )
        return

    # Pending similarity disambiguation — only when reply is an explicit choice
    pending = _get_pending(thread_ts)
    if pending:
        project_name = _parse_pending_choice(prompt, pending)
        if project_name is not None:
            _pop_pending(thread_ts)
            is_new = project_name == pending["proposed_name"]
            label = (
                f"Starting new project *{project_name}*..."
                if is_new
                else f"Continuing *{project_name}*..."
            )
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=label)
            _dispatch_tony_from_pending(
                pending, project_name, user_id, channel, thread_ts, client, t0, run_key
            )
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
        _pop_pending(thread_ts)
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
        ack_text = (
            "Continuing with the project... 🔄"
            if mode == "continue"
            else "On it — this may take several minutes ⏳"
        )
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=ack_text)

        project_name = project.get("project_name", "") if project else ""
        handoff_data = project.get("handoff") if project else None
        workfile_path = project.get("workfile_path", "") if project else ""

        _dispatch_tony(
            mode="continue",
            project_name=project_name,
            tony_prompt=prompt,
            user_id=user_id,
            channel=channel,
            thread_ts=thread_ts,
            client=client,
            t0=t0,
            run_key=run_key,
            handoff_data=handoff_data,
            workfile_path=workfile_path,
            register=False,
        )
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
        jarvis_context = _format_project_list(
            matches, header="Matched projects from registry:"
        )
        ack_text = "I found some matching projects. Let me help you select one... 🔍"
    else:
        jarvis_context = ""
        ack_text = random_jarvis_ack()

    if channel:
        jarvis_context = prepend_slack_location(
            jarvis_context, channel, thread_ts, jarvis_session_id
        )
        jarvis_context += melbourne_datetime_context()
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
            response, new_files, handoff_project, was_cancelled = run_agent(
                jarvis, prompt, client, channel, thread_ts,
                jarvis_session_id, t0, stream_to_slack=True,
                cancel_key=run_key,
            )
            print(f"\n[Jarvis] response length: {len(response)} chars", flush=True)

            if was_cancelled:
                return

            sentinel_path = similarity_sentinel_path(thread_ts)
            if sentinel_path.exists():
                try:
                    with open(sentinel_path) as f:
                        pending = json.load(f)
                    sentinel_path.unlink()
                    _store_pending(thread_ts, pending)
                    _post_similarity_disambiguation(
                        client,
                        channel,
                        thread_ts,
                        pending["proposed_name"],
                        pending["matches"],
                    )
                    print(
                        f"[similarity-gate] paused handoff thread={thread_ts} "
                        f"proposed={pending['proposed_name']} "
                        f"matches={[m['name'] for m in pending['matches']]}",
                        flush=True,
                    )
                except Exception as e:
                    import traceback
                    print(f"[similarity-gate error] {e}\n{traceback.format_exc()}", flush=True)
                post_timing_message(client, channel, thread_ts, perf_counter() - start)
                return

            if handoff_project:
                handoff_path = PROJECTS_DIR / handoff_project / "handoff.json"
                handoff_data = {}
                if handoff_path.exists():
                    with open(handoff_path) as f:
                        handoff_data = json.load(f)
                _dispatch_tony(
                    mode="handoff",
                    project_name=handoff_project,
                    tony_prompt=build_tony_handoff_prompt(handoff_project),
                    user_id=user_id,
                    channel=channel,
                    thread_ts=thread_ts,
                    client=client,
                    t0=t0,
                    run_key=run_key,
                    handoff_data=handoff_data,
                    register=True,
                )
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