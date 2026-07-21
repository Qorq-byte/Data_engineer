"""Storage backend — persistent data layer for the NL2SQL Data Engineer.

Provides MySQL-backed storage for user settings, domain knowledge, query history,
feedback, learning data, and connection configurations.

Usage::

    from app.storage.mysql_store import get_store, init_db

    await init_db()          # create tables if needed
    store = get_store()      # get singleton store instance
    await store.save_setting("llm", {"default_model": "deepseek-v4-flash", ...})
"""

from app.storage.mysql_store import MySQLStore, get_store, init_db

__all__ = ["MySQLStore", "get_store", "init_db"]