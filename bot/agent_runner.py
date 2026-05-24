"""Shared agent run executor with Slack streaming support."""

import threading
import time
from pathlib import Path

from agno.agent import RunEvent

from bot.debug import agent_debug_enabled
from server.paths import find_new_deliverable_files, snapshot_deliverable_files

# Thread-safe cancellation registry: run_key -> should_cancel
_cancellation_registry: dict[str, bool] = {}
_registry_lock = threading.Lock()


def _debug(msg: str) -> None:
    if agent_debug_enabled():
        print(msg, flush=True)


def cancel_key_for(channel: str, thread_ts: str, channel_type: str = "") -> str:
    """Stable key for cancellation — DMs use channel (one active run per DM)."""
    if channel_type == "im":
        return f"dm:{channel}"
    return thread_ts


def register_run(run_key: str) -> None:
    with _registry_lock:
        _cancellation_registry[run_key] = False


def cancel_run(run_key: str) -> bool:
    with _registry_lock:
        if run_key in _cancellation_registry:
            _cancellation_registry[run_key] = True
            return True
        return False


def is_cancelled(run_key: str) -> bool:
    with _registry_lock:
        return _cancellation_registry.get(run_key, False)


def cleanup_run(run_key: str) -> None:
    with _registry_lock:
        _cancellation_registry.pop(run_key, None)

def format_handoff_slack_message(project_name: str) -> str:
    """User-visible handoff confirmation (Slack mrkdwn, not Markdown)."""
    return (
        f"Handed off to Tony for project *{project_name}*. "
        "He'll read up and get back to you shortly."
    )


def _stream_agent_run(
    agent,
    prompt: str,
    client,
    channel: str,
    thread_ts: str,
    session_id: str,
    before: set[Path],
    t0: float = 0,
    stream_to_slack: bool = True,
    cancel_key: str | None = None,
) -> tuple[str, list[Path], str, bool]:
    """Execute a streaming agent run. Returns: (response_text, new_files, handoff_project_name, was_cancelled)."""
    run_key = cancel_key or thread_ts
    register_run(run_key)

    full_response = ""
    first_token = True
    t_last = None
    handoff_project = ""
    was_cancelled = False

    slack_message_ts = None
    last_slack_update = time.time()
    min_update_interval = 1.5

    try:
        stream = agent.run(prompt, stream=True, stream_events=True, session_id=session_id)
        for chunk in stream:
            if is_cancelled(run_key):
                was_cancelled = True
                _debug(f"[CANCEL] Run cancelled for key {run_key}")
                break

            t_last = time.time()

            event = getattr(chunk, "event", None)
            content = getattr(chunk, "content", None)
            _debug(f"[EVENT] {event} | {content[:100] if content else ''}")
            _debug(f"[CHUNK] event={event} content_len={len(content) if content else 0}")

            if event == RunEvent.run_content and content and isinstance(content, str) and len(content) > 0:
                if first_token and t0:
                    _debug(f"[TIMER] first content token: {time.time()-t0:.3f}s")
                    first_token = False
                full_response += content

                if stream_to_slack and full_response.strip():
                    current_time = time.time()
                    time_since_last = current_time - last_slack_update
                    _debug(
                        f"[SLACK STREAM] Checking update: time_since_last={time_since_last:.2f}s, "
                        f"min_interval={min_update_interval}s, msg_ts={slack_message_ts}"
                    )
                    if time_since_last >= min_update_interval:
                        try:
                            if slack_message_ts is None:
                                _debug(f"[SLACK STREAM] Posting initial message to {channel}, thread={thread_ts}")
                                result = client.chat_postMessage(
                                    channel=channel,
                                    thread_ts=thread_ts,
                                    text=full_response.strip() + " ▌",
                                )
                                slack_message_ts = result.get("ts") if result else None
                                _debug(f"[SLACK STREAM] Initial post result: ts={slack_message_ts}")
                            else:
                                _debug(
                                    f"[SLACK STREAM] Updating message ts={slack_message_ts}, "
                                    f"len={len(full_response)}"
                                )
                                client.chat_update(
                                    channel=channel,
                                    ts=slack_message_ts,
                                    text=full_response.strip() + " ▌",
                                )
                                _debug("[SLACK STREAM] Update sent successfully")
                            last_slack_update = current_time
                        except Exception as e:
                            print(f"[SLACK STREAM] ERROR: {type(e).__name__}: {e}", flush=True)
                            import traceback
                            print(traceback.format_exc(), flush=True)

            elif event == RunEvent.model_request_completed:
                _debug(f"[TIMER] ModelRequestCompleted: {time.time()-t0:.3f}s")

            elif event == RunEvent.tool_call_started:
                if agent_debug_enabled():
                    tool_name = getattr(getattr(chunk, "tool", None), "tool_name", "unknown")
                    if tool_name != "handoff_to_tony":
                        tool_args = getattr(getattr(chunk, "tool", None), "tool_args", None)
                        args_preview = str(tool_args)[:200] if tool_args else ""
                        client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=f"🔧 `{tool_name}` — `{args_preview}`",
                        )

            elif event == RunEvent.tool_call_completed:
                tool_name = getattr(getattr(chunk, "tool", None), "tool_name", "unknown")
                tool_obj = getattr(chunk, "tool", None)
                tool_result = ""
                if tool_obj:
                    tool_result = (
                        getattr(tool_obj, "result", "")
                        or getattr(tool_obj, "content", "")
                        or getattr(tool_obj, "output", "")
                        or str(tool_obj)
                    )
                _debug(f"[HANDOFF DEBUG] tool_name: {tool_name}")
                _debug(f"[HANDOFF DEBUG] tool result: {tool_result[:500]}")
                if tool_result and "HANDOFF_READY:" in str(tool_result):
                    try:
                        handoff_project = str(tool_result).split("HANDOFF_READY:")[1].strip().split()[0]
                        _debug(f"[HANDOFF DEBUG] detected handoff project: {handoff_project}")
                    except IndexError:
                        pass

            elif event == RunEvent.run_error:
                error = getattr(chunk, "content", None) or "unknown error"
                print(f"[agent error] {error}")
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"❌ Error: {error}",
                )

            elif event == RunEvent.run_content_completed:
                _debug(f"[TIMER] RunContentCompleted: {time.time()-t0:.3f}s")

    finally:
        cleanup_run(run_key)

    if t_last and t0:
        _debug(f"[TIMER] stream ended: {t_last - t0:.3f}s")

    if stream_to_slack:
        if was_cancelled:
            final_text = (
                full_response.strip() + "\n\n_(⏹️ stopped by user)_"
                if full_response.strip()
                else "_(⏹️ stopped by user)_"
            )
        else:
            final_text = full_response.strip() or "_(done)_"
            if handoff_project:
                final_text = format_handoff_slack_message(handoff_project)
        if slack_message_ts:
            try:
                _debug(f"[SLACK STREAM] Sending final update to ts={slack_message_ts}")
                client.chat_update(
                    channel=channel,
                    ts=slack_message_ts,
                    text=final_text,
                )
                _debug("[SLACK STREAM] Final update sent successfully")
            except Exception as e:
                print(f"[SLACK STREAM] Final update failed: {e}", flush=True)
        elif final_text and final_text != "_(done)_":
            try:
                _debug(f"[SLACK STREAM] No stream message, posting final to {channel}")
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=final_text,
                )
                _debug("[SLACK STREAM] Final message posted")
            except Exception as e:
                print(f"[SLACK STREAM] Final post failed: {e}", flush=True)

    new_files = find_new_deliverable_files(before)
    _debug(f"[find_output_files] Found {len(new_files)} new files: {[str(f) for f in new_files]}")
    return full_response.strip() or "_(no response)_", new_files, handoff_project, was_cancelled


def run_agent(
    agent,
    prompt: str,
    client,
    channel: str,
    thread_ts: str,
    session_id: str,
    t0: float = 0,
    stream_to_slack: bool = True,
    cancel_key: str | None = None,
) -> tuple[str, list[Path], str, bool]:
    """Run an agent with optional Slack streaming (no wall-clock limit)."""
    before = snapshot_deliverable_files()
    return _stream_agent_run(
        agent, prompt, client, channel, thread_ts, session_id, before, t0, stream_to_slack, cancel_key
    )


def upload_files(client, channel: str, thread_ts: str | None, files: list[Path]) -> None:
    """Upload each output file to the Slack conversation."""
    for path in files:
        try:
            client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(path),
                filename=path.name,
                title=path.name,
            )
        except Exception as e:
            print(f"[slack] Failed to upload {path.name}: {e}")


def post_timing_message(client, channel: str, thread_ts: str, elapsed: float) -> None:
    """Post a separate timing message after a streamed response."""
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"_({elapsed:.1f}s)_",
    )
