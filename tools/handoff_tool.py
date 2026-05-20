"""
Handoff tool — allows Jarvis to transfer work to Tony.

This tool packages a project brief and queues it for Tony.
Jarvis only. Tony never has this tool.
"""

import json
import os
from datetime import datetime


def handoff_to_tony(
    goal: str,
    success_markers: list[str],
    constraints: list[str],
    scope: str,
    dept: str,
    project_name: str,
    knowledge_refs: list[str] = [],
    task: str = "",
    slack_channel: str = "",
    thread_ts: str = "",
) -> str:
    """
    Call this when the user has explicitly confirmed the project brief
    and work should begin. Packages the brief and queues it for Tony.

    IMPORTANT: Only call this after the user has said yes, proceed,
    go ahead, or equivalent. Never call speculatively.

    Args:
        goal:            Clearly stated end goal for the project.
        success_markers: List of specific, testable success criteria.
        constraints:     List of explicit constraints. Pass ["none"] if
                         none were stated.
        scope:           "one-off" or "ongoing"
        dept:            "marketing" | "compliance" | "content" | "other"
        project_name:    Kebab-case project name you assign.
                         Example: "affiliate-fitness-q3"
        knowledge_refs:  Knowledge file paths explicitly named by the user.
                         Leave empty [] if unknown — Tony will resolve.
        task:            Optional task id (e.g. "eod_report_init", "eod_report_adjust").
        slack_channel:   Slack channel ID where init was requested (for EOD project).
        thread_ts:       Slack thread ts where init was requested.

    Returns:
        Signal string "HANDOFF_READY:{project_name}" for the bot to
        intercept. Do not interpret this return value yourself.
    """
    brief = {
        "goal": goal,
        "success_markers": success_markers,
        "constraints": constraints,
        "scope": scope,
        "dept": dept,
        "project_name": project_name,
        "knowledge_refs": knowledge_refs,
        "needs_resolution": len(knowledge_refs) == 0,
        "created": datetime.now().isoformat(),
    }
    if task:
        brief["task"] = task
    if slack_channel:
        brief["slack_channel"] = slack_channel
    if thread_ts:
        brief["thread_ts"] = thread_ts

    # Create project directory
    project_dir = f"projects/{project_name}"
    os.makedirs(project_dir, exist_ok=True)

    # Write handoff file for Tony to read on first run
    handoff_path = os.path.join(project_dir, "handoff.json")
    with open(handoff_path, "w") as f:
        json.dump(brief, f, indent=2)

    return f"HANDOFF_READY:{project_name}"
