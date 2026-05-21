"""Shared database for AgentOS scheduler and SchedulerTools."""

from functools import lru_cache

from agno.db.sqlite import SqliteDb

from server.paths import MEMORY_DIR

SCHEDULER_DB_FILE = str(MEMORY_DIR / "agno.db")


@lru_cache(maxsize=1)
def get_scheduler_db() -> SqliteDb:
    """Return the shared SQLite DB used by AgentOS and SchedulerTools."""
    return SqliteDb(db_file=SCHEDULER_DB_FILE)
