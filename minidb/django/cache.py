"""A Django cache backend backed by minidb.

    CACHES = {
        "default": {
            "BACKEND": "minidb.django.cache.MinidbCache",
            "LOCATION": "app.db",
            "TIMEOUT": 300,
        }
    }
"""

from __future__ import annotations

import time
from typing import Any

from django.core.cache.backends.base import BaseCache

from .database import get_database

__all__ = ["MinidbCache"]

DEFAULT_TIMEOUT = 300


class MinidbCache(BaseCache):
    """Stores cache entries as documents in a minidb collection."""

    def __init__(self, location: str = "", params: dict | None = None):
        super().__init__(params or {})
        self._location = location
        self._collection_name = params.get("COLLECTION", "cache") if params else "cache"

    # -- helpers ---------------------------------------------------------

    @property
    def _collection(self):
        database = get_database()
        name = self._collection_name
        if name not in database.list_collection_names():
            database.create_collection(name)
        return database[name]

    def _key(self, key: str, version=None) -> str:
        """The document id for a cache key."""
        return self.make_key(key, version=version)

    # -- the cache interface --------------------------------------------

    def add(self, key: str, value: Any, timeout: int | None = None, version=None) -> bool:
        if self.has_key(key):
            return False
        self.set(key, value, timeout, version)
        return True

    def set(self, key: str, value: Any, timeout: int | None = None, version=None) -> None:
        seconds = self.get_backend_timeout(timeout)
        expires = time.time() + seconds if seconds is not None else None
        self._collection.replace_one(
            {"_id": self._key(key, version)},
            {"_id": self._key(key, version), "value": value, "expires": expires},
            upsert=True,
        )

    def get(self, key: str, default=None, version=None) -> Any:
        document = self._collection.find_one({"_id": self._key(key, version)})
        if document is None:
            return default
        expires = document.get("expires")
        if expires is not None and expires <= time.time():
            self._collection.delete_one({"_id": self._key(key, version)})
            return default
        return document.get("value", default)

    def touch(self, key: str, timeout: int | None = None, version=None) -> bool:
        document = self._collection.find_one({"_id": self._key(key, version)})
        if document is None:
            return False
        seconds = self.get_backend_timeout(timeout)
        document["expires"] = time.time() + seconds if seconds is not None else None
        self._collection.replace_one({"_id": self._key(key, version)}, document)
        return True

    def delete(self, key: str, version=None) -> bool:
        result = self._collection.delete_one({"_id": self._key(key, version)})
        return result.deleted_count > 0

    def get_many(self, keys, version=None) -> dict[str, Any]:
        found = {}
        for key in keys:
            value = self.get(key, version=version)
            if value is not None:
                found[key] = value
        return found

    def set_many(self, data, timeout=None, version=None) -> None:
        for key, value in data.items():
            self.set(key, value, timeout, version)

    def delete_many(self, keys, version=None) -> None:
        for key in keys:
            self.delete(key, version)

    def has_key(self, key: str, version=None) -> bool:
        sentinel = object()
        return self.get(key, sentinel, version) is not sentinel

    def clear(self) -> None:
        self._collection.delete_many({})

    def incr(self, key: str, delta: int = 1, version=None) -> int:
        value = self.get(key, 0, version) or 0
        value += delta
        self.set(key, value, version=version)
        return value

    def decr(self, key: str, delta: int = 1, version=None) -> int:
        return self.incr(key, -delta, version)

    def close(self, **kwargs) -> None:
        """The database is shared, so there is nothing to close here."""
