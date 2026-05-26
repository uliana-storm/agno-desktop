"""SqliteDb subclass that truncates oversized tool results before session persist."""

from typing import Optional, Union

from agno.db.sqlite import SqliteDb
from agno.session import Session

MAX_TOOL_CHARS = 800
TRUNCATION_NOTE = "\n[truncated — re-fetch if needed]"


def _trim_tool_message(msg) -> None:
    if getattr(msg, "role", None) != "tool":
        return

    content = getattr(msg, "content", None)
    if content:
        text = content if isinstance(content, str) else str(content)
        if len(text) > MAX_TOOL_CHARS:
            msg.content = text[:MAX_TOOL_CHARS] + TRUNCATION_NOTE
            if getattr(msg, "compressed_content", None):
                msg.compressed_content = None

    compressed = getattr(msg, "compressed_content", None)
    if compressed:
        text = compressed if isinstance(compressed, str) else str(compressed)
        if len(text) > MAX_TOOL_CHARS:
            msg.compressed_content = text[:MAX_TOOL_CHARS] + TRUNCATION_NOTE


def _trim_session(session: Session) -> Session:
    for run in getattr(session, "runs", None) or []:
        for msg in getattr(run, "messages", None) or []:
            _trim_tool_message(msg)

    for container in (getattr(session, "memory", None), session):
        if container is None:
            continue
        for msg in getattr(container, "messages", None) or []:
            _trim_tool_message(msg)

    return session


class TrimmedSqliteDb(SqliteDb):
    def upsert_session(
        self, session: Session, deserialize: Optional[bool] = True
    ) -> Optional[Union[Session, dict]]:
        return super().upsert_session(_trim_session(session), deserialize)
