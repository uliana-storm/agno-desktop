"""
Tony agent factory — research and execution agent.

Tony is the research specialist. It:
- Has full toolkit: web search, news feeds, CoinGecko, Python, file I/O
- Writes deliverables to output/ and project files to projects/
- Reads from knowledge/ and projects/ for context
- Executes complex research tasks end-to-end
"""

import os
import sys
from pathlib import Path

# Add parent directory to path so tools can be imported
_parent_dir = Path(__file__).parent.parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

from agno.agent import Agent
from agno.db.sqlite import SqliteDb  # TODO: upgrade to SqliteStorage when available
from agno.tools.file_generation import FileGenerationTools
from agno.tools.function import Function as FunctionTool
from agno.compression import CompressionManager
from agno.utils.log import log_info

from agents.llm_logger import LoggedOpenAILike
from agents.task_context import eod_run_instructions, task_context
from server.paths import OUTPUT_DIR, PROJECTS_DIR, REPORTS_DIR, TONY_MEMORY_DB
from tools.brave_search_tool import BraveSearchToolkit
from tools.coingecko_tool import CoinGeckoToolkit
from tools.feed_fetch_tool import NewsFeedToolkit
from tools.python_sandbox import SandboxPythonTools
from tools.slack_blocks_tool import post_eod_report
from tools.slack_notify_tool import post_to_slack
from tools.create_project_schedule_tool import create_project_schedule
from tools.upload_deliverable_tool import upload_deliverable
from tools.scheduler_tools_config import tony_scheduler_tools
from tools.slack_tools_config import tony_slack_tools
from tools.tony_file_toolkits import file_path_guide, tony_file_toolkits
from tools.html_generator import generate_html_report, generate_html_from_markdown


def load_prompt() -> str:
    """Load system prompt from adjacent prompts.md."""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts.md")
    try:
        with open(prompt_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are Tony, a research specialist at Stormrake. Handle research, writing, analysis, and project execution."
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
        session_id: Session identifier (format: tony-{user_id}-{thread_ts})
        additional_context: Optional task context to inject
        workfile_path: Path to project workfile to load as context
        slack_channel: Optional Slack channel for scheduled run delivery
        thread_ts: Optional Slack thread timestamp for scheduled run delivery

    Returns:
        Configured Agent instance
    """
    system_prompt = load_prompt()
    # Core KB (goals/compliance/voice) is read via scoped read_file when needed — not preloaded.
    workfile_context = load_project_workfile(workfile_path)

    db = SqliteDb(db_file=str(TONY_MEMORY_DB))

    if not slack_channel and additional_context and "slack_channel:" in additional_context:
        import re
        # Hardened regex to handle quoted and unquoted values
        channel_match = re.search(r'slack_channel:\s*["\']?([^"\'\n]+)["\']?', additional_context)
        thread_match = re.search(r'thread_ts:\s*["\']?([^"\'\n]+)["\']?', additional_context)
        if channel_match and thread_match:
            slack_channel = channel_match.group(1).strip()
            thread_ts = thread_match.group(1).strip()
            print(f"[tony] RECOVERED slack context from additional_context: {slack_channel}, {thread_ts}")

    if not slack_channel:
        try:
            from agno.db.base import SessionType
            existing_session = db.get_session(session_id=session_id, session_type=SessionType.AGENT)
            if existing_session and existing_session.session_data:
                stored_state = existing_session.session_data.get("session_state", {})
                if stored_state.get("_slack_channel"):
                    slack_channel = stored_state["_slack_channel"]
                    thread_ts = stored_state["_slack_thread_ts"]
                    print(f"[tony] LOADED slack context from session_state: {slack_channel}, {thread_ts}")
        except Exception as e:
            print(f"[tony] Could not load existing session: {e}")

    session_state = {}
    if slack_channel and thread_ts:
        session_state["_slack_channel"] = slack_channel
        session_state["_slack_thread_ts"] = thread_ts
        print("[tony] STORING slack context in session_state for persistence")

    full_context = file_path_guide() + workfile_context
    if additional_context:
        full_context += f"\n\n## Task Context\n{additional_context}"

    import json
    import re

    task = ""
    if additional_context:
        stripped = additional_context.strip()
        if stripped.startswith("{"):
            try:
                task = json.loads(stripped).get("task", "") or ""
            except json.JSONDecodeError:
                pass
        if not task:
            m = re.search(r'"task"\s*:\s*"([^"]+)"', additional_context)
            if m:
                task = m.group(1)
        if not task:
            m = re.search(r"task:\s*(\S+)", additional_context)
            if m:
                task = m.group(1)
        if task in ("eod_report_init", "eod_report_run", "eod_report_adjust"):
            full_context += task_context(task) + eod_run_instructions(task)

    instructions_text = None
    if slack_channel and thread_ts:
        eod_note = ""
        if task in ("eod_report_run", "eod_report_init"):
            eod_note = (
                "\nFor EOD reports use post_eod_report (Block Kit), not post_to_slack for the full report.\n"
            )
        instructions_text = (
            f"\n\n{'='*80}\n"
            f"⚠️  SLACK DELIVERY ⚠️\n"
            f"{'='*80}\n"
            f"Your response text is streamed to Slack automatically.\n\n"
            f"DO NOT call post_to_slack with your full answer — that would duplicate the stream.\n"
            f"After creating deliverable files on disk, call upload_deliverable("
            f"channel='{slack_channel}', scope='projects' or 'output', path=<relative path>, "
            f"thread_ts='{thread_ts}', initial_comment=<one-line summary>). "
            f"Never pass file content inline — do not use upload_file.\n"
            f"{eod_note}"
            f"{'='*80}"
        )
        print("[tony] ADDED CRITICAL INSTRUCTION as instructions parameter")

    model = LoggedOpenAILike(
        id="local-model",
        base_url="http://localhost:8081/v1",
        api_key="local",
        agent_id="tony",
        session_id=session_id,
    )
    log_info(f"[factory:tony] model_url={model.base_url} session_id={session_id}")

    tools: list = [
        *tony_file_toolkits(),
        BraveSearchToolkit(
            api_key=os.environ.get("BRAVE_API_KEY", ""),
        ),
        NewsFeedToolkit(),
        CoinGeckoToolkit(
            api_key=os.environ.get("COINGECKO_API_KEY", ""),
        ),
        SandboxPythonTools(base_dir=Path(OUTPUT_DIR)),
        FileGenerationTools(output_directory=str(REPORTS_DIR)),
        tony_slack_tools(),
        FunctionTool(name="post_eod_report", entrypoint=post_eod_report),
        FunctionTool(name="post_to_slack", entrypoint=post_to_slack),
        FunctionTool(name="upload_deliverable", entrypoint=upload_deliverable),
        FunctionTool(name="generate_html_report", entrypoint=generate_html_report),
        FunctionTool(name="generate_html_from_markdown", entrypoint=generate_html_from_markdown),
        FunctionTool(
            name="create_project_schedule",
            entrypoint=create_project_schedule,
        ),
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
        add_history_to_context=True,
        num_history_runs=2,
        read_chat_history=False,
        read_tool_call_history=False,
        max_tool_calls_from_history=1,
        enable_session_summaries=False,
        add_session_summary_to_context=False,
        update_memory_on_run=False,
        compress_tool_results=True,
        compression_manager=CompressionManager(
            compress_token_limit=40000,
            compress_tool_results_limit=12,
        ),
        add_datetime_to_context=True,
        timezone_identifier="Australia/Melbourne",
        tool_call_limit=1000,
    )
