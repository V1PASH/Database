"""The Database object.

    from minidb import Database

    db = Database("app.db")        # a file on disk
    db = Database()                # in memory, nothing is written

    db.create_collection("users", validator=USER_SCHEMA)
    db.users.insert_one({"name": "Ada", "email": "ada@example.com"})
    db.users.find_one({"email": "ada@example.com"})
"""

from __future__ import annotations

import os
from typing import Any, Iterator

from .collection import Collection
from .engine import Storage
from .errors import CollectionAlreadyExists, CollectionNotFound
from .validation import Validator

__all__ = ["Database"]


class Database:
    """A collection of collections, backed by one storage file."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:", name: str = "minidb") -> None:
        self.name = name
        self.path = str(path)
        self._storage = Storage(self.path)

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Close the underlying storage."""
        self._storage.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Database({self.path!r})"

    # -- accessing collections ------------------------------------------

    def __getitem__(self, name: str) -> Collection:
        return Collection(self, name)

    def __getattr__(self, name: str) -> Collection:
        # Only reached for attributes that do not exist, so db.users works.
        if name.startswith("_"):
            raise AttributeError(name)
        return Collection(self, name)

    def __iter__(self) -> Iterator[str]:
        return iter(self.list_collection_names())

    def __contains__(self, name: str) -> bool:
        return name in self.list_collection_names()

    # -- managing collections -------------------------------------------

    def create_collection(
        self, name: str, validator: Any = None, **kwargs: Any
    ) -> Collection:
        """Create a collection, optionally with a schema validator."""
        if not isinstance(name, str) or not name:
            raise ValueError("a collection name must be a non-empty string")
        if validator is not None and not isinstance(validator, (dict, Validator)):
            raise ValueError("a validator must be a document")

        self._storage.create_collection(name, validator)
        return Collection(self, name)

    def list_collection_names(self) -> list[str]:
        """The names of every collection in the database."""
        return [name for name, _ in self._storage.list_collections()]

    def list_collections(self) -> list[dict]:
        """Information about every collection in the database."""
        return [
            {"name": name, "validator": validator}
            for name, validator in self._storage.list_collections()
        ]

    def drop_collection(self, name: str) -> None:
        """Delete a collection and everything in it."""
        self._storage.drop_collection(name)

    def rename_collection(self, old_name: str, new_name: str) -> Collection:
        """Rename a collection."""
        if not self._storage.collection_exists(old_name):
            raise CollectionNotFound(old_name)
        if self._storage.collection_exists(new_name):
            raise CollectionAlreadyExists(new_name)
        with self._storage.transaction():
            self._storage.rename_collection(old_name, new_name)
        return Collection(self, new_name)

    def set_collection_validator(self, name: str, validator: Any) -> None:
        """Attach (or replace) the schema of a collection."""
        if validator is not None and not isinstance(validator, (dict, Validator)):
            raise ValueError("a validator must be a document")
        schema = validator.schema if isinstance(validator, Validator) else validator
        self._storage.set_validator(name, schema)

    # -- convenience ----------------------------------------------------

    def command(self, command: Any) -> Any:
        """A very small subset of MongoDB's ``db.command``."""
        if not isinstance(command, dict) or len(command) != 1:
            raise ValueError("a command must be a single-key document")
        name, argument = next(iter(command.items()))
        if name in ("ping", "ismaster"):
            return {"ok": 1.0}
        if name == "count":
            collection, filter = argument if isinstance(argument, (list, tuple)) else (argument, None)
            return {"n": self[collection].count_documents(filter), "ok": 1.0}
        if name == "collStats":
            return {
                "ns": argument,
                "count": len(self[argument]),
                "ok": 1.0,
            }
        if name == "create":
            self.create_collection(argument)
            return {"ok": 1.0}
        if name == "drop":
            self.drop_collection(argument)
            return {"ok": 1.0}
        raise NotImplementedError(f"unsupported command: {name}")
