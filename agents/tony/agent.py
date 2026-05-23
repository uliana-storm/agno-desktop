"""
Tony agent factory — research and execution agent.

Tony is the research specialist. It:
- Has full toolkit: web search, news feeds, CoinGecko, Python, file I/O
- Writes deliverables to output/ and project files to projects/
- Reads from knowledge/ and projects/ for context
- Executes complex research tasks end-to-end
"""

import json
import os
import re
import sys
from pathlib import Path

# Add parent directory to path so tools can be imported
_parent_dir = Path(__file__).parent.parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

from agno.agent import Agent
from agno.compression import CompressionManager
from agno.db.sqlite import SqliteDb  # TODO: upgrade to SqliteStorage when available
from agno.tools.file_generation import FileGenerationTools
from agno.tools.function import Function as FunctionTool
from agno.utils.log import log_info

from agents.llm_logger import LoggedOpenAILike
from agents.task_context import melbourne_datetime_context, tony_slack_instructions
from server.paths import OUTPUT_DIR, REPORTS_DIR, TONY_MEMORY_DB
from tools.brave_search_tool import BraveSearchToolkit
from tools.coingecko_tool import CoinGeckoToolkit
from tools.feed_fetch_tool import NewsFeedToolkit
from tools.html_generator import generate_html_from_markdown, generate_html_report
from tools.python_sandbox import SandboxPythonTools
from tools.scheduler_tools_config import tony_scheduler_tools
from tools.slack_fetch_tools import SlackFetchToolkit
from tools.slack_search_tools import SlackSearchToolkit
from tools.tony_file_toolkits import file_path_guide, tony_file_toolkits
from tools.upload_deliverable_tool import upload_deliverable


def load_prompt() -> str:
    """Load system prompt from adjacent prompts.md."""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts.md")
    try:
        with open(prompt_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "You are Tony, a research specialist at Stormrake. "
            "Handle research, writing, analysis, and project execution."
        )
    except Exception as e:
        return f"You are Tony, a research specialist at Stormrake. [Prompt load error: {e}]"


def load_project_workfile(workfile_path: str) -> str:
    """Load project workfile if it exists."""
    if not workfile_path:
        return ""
    try:
        if os.path.exists(workfile_path):
            with open(workfile_path, encoding="utf-8") as f:
                return f"\n\n## Project Workfile\n{f.read()}"
    except Exception as e:
        return f"\n\n## Project Workfile\n[Error loading workfile: {e}]"
    return ""


def create_tony_agent(
    session_id: str,
    additional_context: str = "",
    workfile_path: str = "",
    slack_channel: str = "",
    thread_ts: str = "",
) -> Agent:
    """
    Tony research agent — full toolkit, writes to output/ and projects/.

    Args:
        session_id:         Session identifier (format: tony-{user_id}-{thread_ts})
        additional_context: Optional task context to inject
        workfile_path:      Path to project workfile to load as context
        slack_channel:      Optional Slack channel for scheduled run delivery
        thread_ts:          Optional Slack thread timestamp for scheduled run delivery

    Returns:
        Configured Agent instance
    """
    system_prompt    = load_prompt()
    workfile_context = load_project_workfile(workfile_path)

    db = SqliteDb(db_file=str(TONY_MEMORY_DB))

    # --- Slack context recovery ---

    if not slack_channel and additional_context and "slack_channel:" in additional_context:
        channel_match = re.search(r'slack_channel:\s*["\']?([^"\'\n]+)["\']?', additional_context)
        thread_match  = re.search(r'thread_ts:\s*["\']?([^"\'\n]+)["\']?', additional_context)
        if channel_match and thread_match:
            slack_channel = channel_match.group(1).strip()
            thread_ts     = thread_match.group(1).strip()
            print(f"[tony] RECOVERED slack context from additional_context: {slack_channel}, {thread_ts}")

    if not slack_channel:
        try:
            from agno.db.base import SessionType
            existing_session = db.get_session(
                session_id=session_id, session_type=SessionType.AGENT
            )
            if existing_session and existing_session.session_data:
                stored_state = existing_session.session_data.get("session_state", {})
                if stored_state.get("_slack_channel"):
                    slack_channel = stored_state["_slack_channel"]
                    thread_ts     = stored_state["_slack_thread_ts"]
                    print(
                        f"[tony] LOADED slack context from session_state: "
                        f"{slack_channel}, {thread_ts}"
                    )
        except Exception as e:
            print(f"[tony] Could not load existing session: {e}")

    session_state = {}
    if slack_channel and thread_ts:
        session_state["_slack_channel"]    = slack_channel
        session_state["_slack_thread_ts"]  = thread_ts
        print("[tony] STORING slack context in session_state for persistence")

    # --- Build additional_context ---

    full_context = file_path_guide() + workfile_context
    if additional_context:
        full_context += f"\n\n## Task Context\n{additional_context}"

    # --- Build instructions ---

    instructions_text = None
    if slack_channel and thread_ts:
        instructions_text = (
            f"\n\n{'='*80}\n"
            f"⚠️  SLACK DELIVERY ⚠️\n"
            f"{'='*80}\n"
            f"Your response text is streamed to Slack automatically — "
            f"do not send it again via any tool.\n\n"
            f"After creating deliverable files on disk, call:\n"
            f"upload_deliverable("
            f"channel='{slack_channel}', "
            f"scope='projects' or 'output', "
            f"path=<relative path>, "
            f"thread_ts='{thread_ts}', "
            f"initial_comment=<one-line summary>)\n\n"
            f"Never pass file content inline.\n"
            f"{'='*80}"
        )
        print("[tony] ADDED CRITICAL INSTRUCTION as instructions parameter")

    # --- Model ---

    model = LoggedOpenAILike(
        id="local-model",
        base_url="http://localhost:8081/v1",
        api_key="local",
        agent_id="tony",
        session_id=session_id,
    )
    log_info(f"[factory:tony] model_url={model.base_url} session_id={session_id}")

    # --- Tools ---

    tools: list = [
        *tony_file_toolkits(),
        BraveSearchToolkit(api_key=os.environ.get("BRAVE_API_KEY", "")),
        NewsFeedToolkit(),
        CoinGeckoToolkit(api_key=os.environ.get("COINGECKO_API_KEY", "")),
        SandboxPythonTools(base_dir=Path(OUTPUT_DIR)),
        FileGenerationTools(output_directory=str(REPORTS_DIR)),
        SlackFetchToolkit(),
        SlackSearchToolkit(),
        FunctionTool(name="upload_deliverable",        entrypoint=upload_deliverable),
        FunctionTool(name="generate_html_report",      entrypoint=generate_html_report),
        FunctionTool(name="generate_html_from_markdown", entrypoint=generate_html_from_markdown),
        tony_scheduler_tools(),
    ]

    return Agent(
        name="Tony",
        model=model,
        description=system_prompt,
        additional_context=full_context,
        instructions=instructions_text,
        session_state=session_state,
        tools=tools,
        db=db,
        session_id=session_id,
        # History
        add_history_to_context=True,
        num_history_runs=3,
        read_chat_history=False,
        read_tool_call_history=False,
        max_tool_calls_from_history=1,
        # Session summaries — disabled to save one LLM call per turn
        enable_session_summaries=False,
        add_session_summary_to_context=False,
        # Memory
        update_memory_on_run=False,
        # Compression
        compress_tool_results=True,
        compression_manager=CompressionManager(
            compress_token_limit=40_000,
            compress_tool_results_limit=12,
        ),
        # Misc
        add_datetime_to_context=True,
        timezone_identifier="Australia/Melbourne",
        tool_call_limit=50,
    )