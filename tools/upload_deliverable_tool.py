"""Upload deliverables from disk — avoids passing large file bodies in tool-call JSON."""

import json
import os
from pathlib import Path
from typing import Literal, Optional

from slack_sdk import WebClient

from tools.tony_file_toolkits import resolve_scoped_path

FileScope = Literal["projects", "output"]


def _client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not set")
    return WebClient(token=token)


def upload_deliverable(
    channel: str,
    scope: FileScope,
    path: str,
    thread_ts: str = "",
    filename: Optional[str] = None,
    initial_comment: Optional[str] = None,
) -> str:
    """Upload a file from projects/ or output/ to Slack (reads from disk).

    Do not pass file content in this tool — only scope and path. Use after save_file
    or FileGenerationTools created the file on disk.

    Args:
        channel: Slack channel ID.
        scope: projects or output.
        path: Relative path within scope (e.g. market-sentiment/workfile.md or reports/foo.html).
        thread_ts: Thread timestamp to attach the file to.
        filename: Optional display name (defaults to path basename).
        initial_comment: Optional one-line message with the upload.
    """
    resolved, err = resolve_scoped_path(scope, path)
    if err:
        return json.dumps({"ok": False, "error": err})
    if not resolved.is_file():
        return json.dumps(
            {"ok": False, "error": f"not a file: {resolved} (use save_file first)"}
        )

    name = (filename or resolved.name).strip() or resolved.name
    try:
        client = _client()
        kwargs: dict = {
            "channel": channel,
            "file": str(resolved),
            "filename": name,
            "title": name,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        if initial_comment:
            kwargs["initial_comment"] = initial_comment
        client.files_upload_v2(**kwargs)
        return json.dumps({"ok": True, "filename": name, "path": str(resolved)})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
