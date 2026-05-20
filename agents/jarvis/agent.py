"""
Jarvis agent factory — intake and routing agent.

Jarvis is the first point of contact. It:
- Reads from knowledge/ and projects/index.md (read-only)
- Does NOT have search, news feed, or Python tools
- Uses more history (5 runs) for conversational context
- Routes requests to Tony or handles knowledge queries directly
"""

import os
import sys
from typing import Optional

# Add parent directory to path so tools can be imported
_parent_dir = Path(__file__).parent.parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

from agno.agent import Agent
from agno.db.sqlite import SqliteDb  # TODO: upgrade to SqliteStorage when available
from agno.tools.function import Function as FunctionTool
from agno.compression import CompressionManager
from agno.utils.log import log_info

from agents.llm_logger import LoggedOpenAILike
from agents.task_context import (
    jarvis_slack_instructions,
    melbourne_datetime_context,
)
from server.paths import JARVIS_MEMORY_DB
from tools.handoff_tool import handoff_to_tony
from tools.scheduler_tools_config import jarvis_scheduler_tools
from tools.tony_file_toolkits import jarvis_file_toolkit
from tools.schedule_reminder_tool import schedule_reminder_in_minutes
from tools.slack_notify_tool import post_to_slack
from tools.slack_read_tool import SlackReadToolkit
from tools.slack_tools_config import jarvis_slack_tools


def load_prompt() -> str:
    """Load system prompt from adjacent prompts.md."""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts.md")
    with open(prompt_path) as f:
        return f.read()


def create_jarvis_agent(
    session_id: str,
    additional_context: str = "",
    slack_channel: Optional[str] = None,
    thread_ts: Optional[str] = None,
    is_scheduled: bool = False,
) -> Agent:
    """
    Jarvis intake agent — FileTools only, read-only access.

    Args:
        session_id: Session identifier (format: jarvis-{user_id})
        additional_context: Optional additional context to inject
        slack_channel: Optional Slack channel for scheduled run delivery
        thread_ts: Optional Slack thread timestamp for scheduled run delivery
        is_scheduled: True if this is a scheduled run (shows CRITICAL instruction block)

    Returns:
        Configured Agent instance
    """
    import time
    t_factory_start = time.time()
    
    system_prompt = load_prompt()
    # Note: core KB files (goals.md, compliance.md, voice.md) are NOT pre-loaded
    # Jarvis reads them reactively via FileTools when needed
    
    # Database for session persistence
    db = SqliteDb(db_file=str(JARVIS_MEMORY_DB))
    
    # Try to extract slack info from additional_context if not provided directly
    # This handles polling where agent is recreated without factory_input
    if not slack_channel and additional_context and "slack_channel:" in additional_context:
        import re
        channel_match = re.search(r'slack_channel:\s*(\S+)', additional_context)
        thread_match = re.search(r'thread_ts:\s*(\S+)', additional_context)
        if channel_match and thread_match:
            slack_channel = channel_match.group(1)
            thread_ts = thread_match.group(1)
            print(f"[jarvis] RECOVERED slack context from additional_context: {slack_channel}, {thread_ts}")
    
    # Also try to load from existing session (for future runs after first completion)
    if not slack_channel:
        try:
            from agno.db.base import SessionType
            existing_session = db.get_session(session_id=session_id, session_type=SessionType.AGENT)
            if existing_session and existing_session.session_data:
                stored_state = existing_session.session_data.get("session_state", {})
                if stored_state.get("_slack_channel"):
                    slack_channel = stored_state["_slack_channel"]
                    thread_ts = stored_state["_slack_thread_ts"]
                    print(f"[jarvis] LOADED slack context from session_state: {slack_channel}, {thread_ts}")
        except Exception as e:
            print(f"[jarvis] Could not load existing session: {e}")
    
    # Initialize session state - will be merged with existing state by agno
    session_state = {}
    if slack_channel and thread_ts:
        session_state["_slack_channel"] = slack_channel
        session_state["_slack_thread_ts"] = thread_ts
        print(f"[jarvis] STORING slack context in session_state for persistence")
    
    # Inject Melbourne datetime for scheduled runs and when scheduling context is present
    if is_scheduled or "## Scheduling context" in (additional_context or ""):
        if "## Current time (Australia/Melbourne)" not in (additional_context or ""):
            additional_context = (additional_context or "") + melbourne_datetime_context()

    # Build instructions parameter - this is rendered AFTER memories/summaries
    # CRITICAL block only appears for scheduled runs (is_scheduled=True)
    instructions_text = None
    if is_scheduled and slack_channel and thread_ts:
        instructions_text = (
            f"\n\n{'='*80}\n"
            f"⚠️  SCHEDULED RUN — SLACK DELIVERY ⚠️\n"
            f"{'='*80}\n"
            f"Your response text is streamed to Slack automatically.\n\n"
            f"DO NOT call post_to_slack with your full answer — that would duplicate the stream.\n"
            f"If you create deliverable files, use upload_file when applicable.\n"
            f"Slack channel: {slack_channel}\n"
            f"Thread ts: {thread_ts}\n"
            f"{'='*80}"
        )
        print(f"[jarvis] ADDED CRITICAL INSTRUCTION (scheduled run)")
    elif slack_channel and thread_ts:
        print("[jarvis] Regular Slack message with thread context")
        instructions_text = jarvis_slack_instructions()

    # Create logged model for audit tracking
    model = LoggedOpenAILike(
        id="local-model",
        base_url="http://localhost:8082/v1",
        api_key="local",
        extra_body={"thinking": {"type": "disabled"}},
        agent_id="jarvis",
        session_id=session_id,
    )
    log_info(f"[factory:jarvis] model_url={model.base_url} session_id={session_id}")

    agent = Agent(
        name="Jarvis",
        model=model,
        description=system_prompt,
        additional_context=additional_context,
        instructions=instructions_text,  # Rendered AFTER memories/summaries
        session_state=session_state,
        tools=[
            jarvis_file_toolkit(),
            jarvis_slack_tools(),
            SlackReadToolkit(),
            FunctionTool(name="handoff_to_tony", entrypoint=handoff_to_tony),
            FunctionTool(
                name="schedule_reminder_in_minutes",
                entrypoint=schedule_reminder_in_minutes,
            ),
            FunctionTool(name="post_to_slack", entrypoint=post_to_slack),
            jarvis_scheduler_tools(),
        ],
        db=db,  # Use the db instance created above
        session_id=session_id,
        reasoning=False,
        # History — reduced to prevent old patterns from influencing responses
        add_history_to_context=True,
        num_history_runs=2,
        read_chat_history=False,
        read_tool_call_history=False,
        # Session summaries — DISABLED to save one LLM call per turn
        enable_session_summaries=False,
        add_session_summary_to_context=False,
        # Memory
        update_memory_on_run=False,
        # Compression — count-based, Jarvis tool results are small
        compress_tool_results=True,
        compression_manager=CompressionManager(
            compress_tool_results_limit=8,
        ),
        # Misc
        add_datetime_to_context=False,  # Manually injected only for scheduled runs
        timezone_identifier="Australia/Melbourne",
        tool_call_limit=15,
    )
    
    print(f"[tools] {[t.name for t in agent.tools]}", flush=True)
    print(f"[TIMER] factory create_jarvis_agent: {time.time()-t_factory_start:.3f}s", flush=True)
    return agent
