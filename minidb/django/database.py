"""The database handle Django uses.

The path comes from the settings::

    MINIDB_PATH = "app.db"

or, equivalently::

    MINIDB = {"PATH": "app.db"}
"""

from __future__ import annotations

from typing import Any

from ..database import Database

__all__ = ["get_database", "set_database", "reset_database"]

_database: Database | None = None


def get_database() -> Database:
    """The database minidb models are stored in."""
    global _database
    if _database is None:
        from django.conf import settings

        path = getattr(settings, "MINIDB_PATH", None)
        if path is None:
            config: Any = getattr(settings, "MINIDB", {}) or {}
            path = config.get("PATH", "app.db")
        _database = Database(path)
    return _database


def set_database(database: Database) -> None:
    """Use a specific database (useful in tests)."""
    global _database
    _database = database


def reset_database() -> None:
    """Forget the current database, so the next call reopens it."""
    global _database
    if _database is not None:
        try:
            _database.close()
        except Exception:  # pragma: no cover - best effort
            pass
    _database = None
