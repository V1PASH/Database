"""Opening and preparing the database.

    from app.database import connect

    db = connect()                    # in memory, for tests
    db = connect("app.db")            # a file on disk

``connect`` creates every collection with its schema, and the indexes the
application relies on. Running it twice is harmless.
"""

from __future__ import annotations

import os
from typing import Any

from minidb import Database
from schemas.collections import SCHEMAS

__all__ = ["connect", "DEFAULT_PATH"]

DEFAULT_PATH = os.environ.get("MINIDB_PATH", "app.db")


def connect(path: str | os.PathLike[str] | None = None) -> Database:
    """Open the database and make sure the schema and indexes are in place."""
    db = Database(str(path) if path is not None else DEFAULT_PATH)

    for name, validator in SCHEMAS.items():
        if name not in db.list_collection_names():
            db.create_collection(name, validator=validator)
        else:
            # Keep the schema in step with the code.
            db.set_collection_validator(name, validator)

        collection = db[name]
        for index in _indexes_for(name):
            existing = {entry["name"] for entry in collection.list_indexes()}
            if index["name"] not in existing:
                collection.create_index(
                    index["key"], unique=index["unique"], name=index["name"]
                )

    return db


def _indexes_for(collection: str) -> list[dict[str, Any]]:
    """The indexes each collection needs."""
    if collection == "users":
        return [
            {"name": "email_1", "key": "email", "unique": True},
            {"name": "role_1", "key": "role", "unique": False},
        ]
    if collection == "projects":
        return [
            {"name": "slug_1", "key": "slug", "unique": True},
            # A compound index: "the tasks of this project that are still open".
            {
                "name": "owner_id_1_status_1",
                "key": [("owner_id", 1), ("status", 1)],
                "unique": False,
            },
        ]
    if collection == "tasks":
        return [
            # "the tasks of a project, by status" -- one index, two questions.
            {
                "name": "project_id_1_status_1",
                "key": [("project_id", 1), ("status", 1)],
                "unique": False,
            },
            {"name": "assignee_id_1", "key": "assignee_id", "unique": False},
        ]
    if collection == "notes":
        return [
            {"name": "project_id_1", "key": "project_id", "unique": False},
            {"name": "pinned_1", "key": "pinned", "unique": False},
        ]
    return []
