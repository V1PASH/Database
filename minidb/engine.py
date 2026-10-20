"""The storage engine.

SQLite is used purely as a **durability layer** -- it stores documents as
JSON text and keeps the index tables in shape. Everything that defines how
the database *behaves* (query semantics, index layout, uniqueness rules)
lives in this project, not in SQLite.

Layout::

    _minidb_collections   one row per collection (name + validator)
    _minidb_documents     the documents themselves, keyed by (collection, id)
    _minidb_indexes       the index definitions (one row per index)
    _minidb_index_entries the index contents, a B-tree keyed by
                          (collection, index, key, document id)
    _minidb_index_overflow documents too large to index completely
    _minidb_counters      monotonic counters (insertion order)
    _minidb_meta          schema and key-encoding versions
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
from typing import Any, Iterable, Iterator

from .bson import dumps, loads
from .errors import (
    CollectionAlreadyExists,
    CollectionNotFound,
    DuplicateKeyError,
    WriteError,
)
from .index import encode_key, index_entries_for_paths

__all__ = ["Storage", "doc_key"]

# Bumped whenever the on-disk layout or the index key encoding changes.
# On opening an older file the affected tables are migrated or rebuilt.
SCHEMA_VERSION = "2"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS _minidb_collections (
    name        TEXT PRIMARY KEY,
    validator   TEXT,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS _minidb_documents (
    collection  TEXT NOT NULL,
    doc_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    data        TEXT NOT NULL,
    PRIMARY KEY (collection, doc_id)
);

CREATE INDEX IF NOT EXISTS _minidb_documents_seq
    ON _minidb_documents (collection, seq);

CREATE TABLE IF NOT EXISTS _minidb_indexes (
    collection  TEXT NOT NULL,
    name        TEXT NOT NULL,
    paths       TEXT NOT NULL,
    is_unique   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, name)
);

CREATE TABLE IF NOT EXISTS _minidb_index_entries (
    collection  TEXT NOT NULL,
    index_name  TEXT NOT NULL,
    key         BLOB NOT NULL,
    doc_id      TEXT NOT NULL,
    PRIMARY KEY (collection, index_name, key, doc_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS _minidb_index_overflow (
    collection  TEXT NOT NULL,
    index_name  TEXT NOT NULL,
    doc_id      TEXT NOT NULL,
    PRIMARY KEY (collection, index_name, doc_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS _minidb_counters (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS _minidb_meta (
    name   TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

_INDEX_COLUMNS = """
CREATE TABLE _minidb_indexes (
    collection  TEXT NOT NULL,
    name        TEXT NOT NULL,
    paths       TEXT NOT NULL,
    is_unique   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, name)
)
"""


def doc_key(value: Any) -> str:
    """A stable, type-aware key for a document's ``_id``.

    ``1`` and ``"1"`` must not collide, so the type is part of the key.
    """
    from .comparison import is_array, is_document
    from .objectid import ObjectId

    if isinstance(value, ObjectId):
        return "oid:" + str(value)
    if value is None:
        return "null:"
    if isinstance(value, bool):
        return "bool:" + ("1" if value else "0")
    if isinstance(value, int):
        return "int:" + str(value)
    if isinstance(value, float):
        return "flt:" + repr(value)
    if isinstance(value, str):
        return "str:" + value
    if isinstance(value, (bytes, bytearray)):
        return "bin:" + bytes(value).hex()
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "date:" + value.isoformat()
    if is_array(value):
        return "arr:" + dumps(list(value))
    if is_document(value):
        return "doc:" + dumps(value)
    return "other:" + repr(value)


class Storage:
    """Low level persistence for minidb."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._depth = 0
        self._connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)
        self._migrate()

    # -- lifecycle ----------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- migration ----------------------------------------------------

    def _columns(self, table: str) -> set[str]:
        return {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        }

    def _migrate(self) -> None:
        """Bring an older database file up to the current layout."""
        with self._lock:
            columns = self._columns("_minidb_indexes")
            if "paths" not in columns:
                # The first version stored a single field per index.
                self._connection.execute(
                    "ALTER TABLE _minidb_indexes RENAME TO _minidb_indexes_old"
                )
                self._connection.execute(_INDEX_COLUMNS)
                rows = self._connection.execute(
                    "SELECT collection, name, path, is_unique FROM _minidb_indexes_old"
                ).fetchall()
                for row in rows:
                    self._connection.execute(
                        "INSERT INTO _minidb_indexes (collection, name, paths, is_unique) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            row["collection"],
                            row["name"],
                            json.dumps([row["path"]]),
                            row["is_unique"],
                        ),
                    )
                self._connection.execute("DROP TABLE _minidb_indexes_old")

            version = self._meta("schema_version")
            if version != SCHEMA_VERSION:
                # The key encoding changed, so every index has to be rebuilt
                # from the documents it covers.
                self._connection.execute("DELETE FROM _minidb_index_entries")
                self._connection.execute("DELETE FROM _minidb_index_overflow")
                self._rebuild_indexes()
                self._set_meta("schema_version", SCHEMA_VERSION)

    def _meta(self, name: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM _minidb_meta WHERE name = ?", (name,)
        ).fetchone()
        return row["value"] if row else None

    def _set_meta(self, name: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO _minidb_meta (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
        )

    def _rebuild_indexes(self) -> None:
        for name, paths, _unique in self.list_indexes_all():
            for doc_id, data in self._scan_raw(name):
                self._index_document(name, doc_id, loads(data))

    # -- transactions -------------------------------------------------

    class _Transaction:
        def __init__(self, storage: "Storage") -> None:
            self._storage = storage

        def __enter__(self) -> "Storage":
            self._storage._lock.acquire()
            if self._storage._depth == 0:
                self._storage._connection.execute("BEGIN IMMEDIATE")
            self._storage._depth += 1
            return self._storage

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            try:
                self._storage._depth -= 1
                if self._storage._depth == 0:
                    if exc_type is None:
                        self._storage._connection.execute("COMMIT")
                    else:
                        self._storage._connection.execute("ROLLBACK")
            finally:
                self._storage._lock.release()

    def transaction(self) -> "Storage._Transaction":
        """Run a block of writes atomically (transactions may be nested)."""
        return Storage._Transaction(self)

    # -- collections --------------------------------------------------

    def create_collection(self, name: str, validator: Any = None) -> None:
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM _minidb_collections WHERE name = ?", (name,)
            ).fetchone()
            if exists:
                raise CollectionAlreadyExists(name)
            self._connection.execute(
                "INSERT INTO _minidb_collections (name, validator, created_at) "
                "VALUES (?, ?, ?)",
                (
                    name,
                    dumps(validator) if validator is not None else None,
                    _dt.datetime.now().timestamp(),
                ),
            )

    def drop_collection(self, name: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM _minidb_documents WHERE collection = ?", (name,)
            )
            self._connection.execute(
                "DELETE FROM _minidb_index_entries WHERE collection = ?", (name,)
            )
            self._connection.execute(
                "DELETE FROM _minidb_index_overflow WHERE collection = ?", (name,)
            )
            self._connection.execute(
                "DELETE FROM _minidb_indexes WHERE collection = ?", (name,)
            )
            self._connection.execute(
                "DELETE FROM _minidb_collections WHERE name = ?", (name,)
            )

    def list_collections(self) -> list[tuple[str, Any]]:
        rows = self._connection.execute(
            "SELECT name, validator FROM _minidb_collections ORDER BY name"
        ).fetchall()
        return [
            (row["name"], loads(row["validator"]) if row["validator"] else None)
            for row in rows
        ]

    def collection_exists(self, name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM _minidb_collections WHERE name = ?", (name,)
        ).fetchone()
        return row is not None

    def get_validator(self, name: str) -> Any:
        row = self._connection.execute(
            "SELECT validator FROM _minidb_collections WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise CollectionNotFound(name)
        return loads(row["validator"]) if row["validator"] else None

    def set_validator(self, name: str, validator: Any) -> None:
        with self._lock:
            if not self.collection_exists(name):
                raise CollectionNotFound(name)
            self._connection.execute(
                "UPDATE _minidb_collections SET validator = ? WHERE name = ?",
                (dumps(validator) if validator is not None else None, name),
            )

    def rename_collection(self, old_name: str, new_name: str) -> None:
        """Move a collection (and all of its data) under a new name."""
        with self._lock:
            self._connection.execute(
                "UPDATE _minidb_documents SET collection = ? WHERE collection = ?",
                (new_name, old_name),
            )
            self._connection.execute(
                "UPDATE _minidb_index_entries SET collection = ? WHERE collection = ?",
                (new_name, old_name),
            )
            self._connection.execute(
                "UPDATE _minidb_index_overflow SET collection = ? WHERE collection = ?",
                (new_name, old_name),
            )
            self._connection.execute(
                "UPDATE _minidb_indexes SET collection = ? WHERE collection = ?",
                (new_name, old_name),
            )
            self._connection.execute(
                "UPDATE _minidb_collections SET name = ? WHERE name = ?",
                (new_name, old_name),
            )

    # -- counters -----------------------------------------------------

    def _next_seq(self) -> int:
        self._connection.execute(
            "INSERT INTO _minidb_counters (name, value) VALUES ('seq', 0) "
            "ON CONFLICT(name) DO NOTHING"
        )
        self._connection.execute(
            "UPDATE _minidb_counters SET value = value + 1 WHERE name = 'seq'"
        )
        row = self._connection.execute(
            "SELECT value FROM _minidb_counters WHERE name = 'seq'"
        ).fetchone()
        return int(row["value"])

    # -- indexes ------------------------------------------------------

    def create_index(
        self, collection: str, name: str, paths: list[str], unique: bool = False
    ) -> None:
        with self.transaction():
            if not self.collection_exists(collection):
                raise CollectionNotFound(collection)

            duplicate = self._connection.execute(
                "SELECT name FROM _minidb_indexes WHERE collection = ? AND paths = ?",
                (collection, json.dumps(list(paths))),
            ).fetchone()
            if duplicate:
                raise WriteError(
                    f"an index on {paths!r} already exists for {collection!r} "
                    f"(named {duplicate['name']!r})"
                )

            self._connection.execute(
                "INSERT INTO _minidb_indexes (collection, name, paths, is_unique) "
                "VALUES (?, ?, ?, ?)",
                (collection, name, json.dumps(list(paths)), 1 if unique else 0),
            )

            # Backfill the index from the documents already stored.
            for doc_id, data in self._scan_raw(collection):
                self._index_document(collection, doc_id, loads(data))

    def _key_taken(
        self, collection: str, index_name: str, key: bytes, exclude_doc: str | None
    ) -> bool:
        if exclude_doc is None:
            row = self._connection.execute(
                "SELECT 1 FROM _minidb_index_entries "
                "WHERE collection = ? AND index_name = ? AND key = ? LIMIT 1",
                (collection, index_name, key),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT 1 FROM _minidb_index_entries "
                "WHERE collection = ? AND index_name = ? AND key = ? AND doc_id != ? LIMIT 1",
                (collection, index_name, key, exclude_doc),
            ).fetchone()
        return row is not None

    def drop_index(self, collection: str, name: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM _minidb_index_entries "
                "WHERE collection = ? AND index_name = ?",
                (collection, name),
            )
            self._connection.execute(
                "DELETE FROM _minidb_index_overflow "
                "WHERE collection = ? AND index_name = ?",
                (collection, name),
            )
            self._connection.execute(
                "DELETE FROM _minidb_indexes WHERE collection = ? AND name = ?",
                (collection, name),
            )

    def list_indexes(self, collection: str) -> list[tuple[str, list[str], bool]]:
        """The indexes of a collection: ``(name, fields, unique)``."""
        rows = self._connection.execute(
            "SELECT name, paths, is_unique FROM _minidb_indexes "
            "WHERE collection = ? ORDER BY name",
            (collection,),
        ).fetchall()
        return [
            (row["name"], json.loads(row["paths"]), bool(row["is_unique"]))
            for row in rows
        ]

    def list_indexes_all(self) -> list[tuple[str, list[str], bool]]:
        """The indexes of every collection."""
        rows = self._connection.execute(
            "SELECT collection, name, paths, is_unique FROM _minidb_indexes"
        ).fetchall()
        return [
            (row["collection"], json.loads(row["paths"]), bool(row["is_unique"]))
            for row in rows
        ]

    def index_definitions(self, collection: str) -> list[tuple[str, tuple[str, ...]]]:
        """``(name, fields)`` for every index of a collection."""
        return [(name, tuple(paths)) for name, paths, _ in self.list_indexes(collection)]

    # -- documents ----------------------------------------------------

    def _scan_raw(self, collection: str) -> Iterator[tuple[str, str]]:
        cursor = self._connection.execute(
            "SELECT doc_id, data FROM _minidb_documents WHERE collection = ? "
            "ORDER BY seq",
            (collection,),
        )
        for row in cursor:
            yield row["doc_id"], row["data"]

    def scan(self, collection: str) -> Iterator[tuple[str, dict]]:
        """Every document in the collection, in insertion order."""
        for doc_id, data in self._scan_raw(collection):
            yield doc_id, loads(data)

    def count(self, collection: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM _minidb_documents WHERE collection = ?",
            (collection,),
        ).fetchone()
        return int(row["total"])

    def get(self, collection: str, doc_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT data FROM _minidb_documents WHERE collection = ? AND doc_id = ?",
            (collection, doc_id),
        ).fetchone()
        return loads(row["data"]) if row else None

    def get_many(self, collection: str, doc_ids: Iterable[str]) -> list[dict]:
        """Fetch several documents at once, keeping insertion order."""
        ids = list(dict.fromkeys(doc_ids))
        if not ids:
            return []

        self._connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _minidb_candidates (doc_id TEXT PRIMARY KEY)"
        )
        self._connection.execute("DELETE FROM _minidb_candidates")
        self._connection.executemany(
            "INSERT OR IGNORE INTO _minidb_candidates (doc_id) VALUES (?)",
            [(doc_id,) for doc_id in ids],
        )
        cursor = self._connection.execute(
            "SELECT d.data FROM _minidb_documents d "
            "JOIN _minidb_candidates c ON c.doc_id = d.doc_id "
            "WHERE d.collection = ? ORDER BY d.seq",
            (collection,),
        )
        return [loads(row["data"]) for row in cursor]

    def insert(self, collection: str, doc_id: str, document: dict) -> None:
        """Insert a document and maintain every index."""
        with self._lock:
            existing = self._connection.execute(
                "SELECT 1 FROM _minidb_documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            ).fetchone()
            if existing:
                raise DuplicateKeyError(
                    f"a document with the id {doc_id!r} already exists"
                )

            self._connection.execute(
                "INSERT INTO _minidb_documents (collection, doc_id, seq, data) "
                "VALUES (?, ?, ?, ?)",
                (collection, doc_id, self._next_seq(), dumps(document)),
            )
            self._index_document(collection, doc_id, document)

    def replace(self, collection: str, doc_id: str, document: dict) -> None:
        """Replace a document, keeping the indexes in sync."""
        with self._lock:
            self._remove_index_entries(collection, doc_id)
            self._connection.execute(
                "UPDATE _minidb_documents SET data = ? WHERE collection = ? AND doc_id = ?",
                (dumps(document), collection, doc_id),
            )
            self._index_document(collection, doc_id, document)

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            self._remove_index_entries(collection, doc_id)
            self._connection.execute(
                "DELETE FROM _minidb_documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            )

    # -- index maintenance --------------------------------------------

    def _index_document(self, collection: str, doc_id: str, document: dict) -> None:
        for name, paths, unique in self.list_indexes(collection):
            entries, overflow = index_entries_for_paths(document, paths)
            if overflow:
                # Too many combinations to index: remember the document so
                # that queries still find it.
                self._connection.execute(
                    "INSERT OR IGNORE INTO _minidb_index_overflow "
                    "(collection, index_name, doc_id) VALUES (?, ?, ?)",
                    (collection, name, doc_id),
                )
            for key in entries:
                if unique and self._key_taken(collection, name, key, doc_id):
                    raise DuplicateKeyError(
                        f"duplicate key value violates the unique index {name!r} "
                        f"on {collection!r} (fields {paths!r})"
                    )
                self._connection.execute(
                    "INSERT OR IGNORE INTO _minidb_index_entries "
                    "(collection, index_name, key, doc_id) VALUES (?, ?, ?, ?)",
                    (collection, name, key, doc_id),
                )

    def _remove_index_entries(self, collection: str, doc_id: str) -> None:
        self._connection.execute(
            "DELETE FROM _minidb_index_entries WHERE collection = ? AND doc_id = ?",
            (collection, doc_id),
        )
        self._connection.execute(
            "DELETE FROM _minidb_index_overflow WHERE collection = ? AND doc_id = ?",
            (collection, doc_id),
        )

    # -- index lookups ------------------------------------------------

    def _overflow_ids(self, collection: str, index_name: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT doc_id FROM _minidb_index_overflow "
            "WHERE collection = ? AND index_name = ?",
            (collection, index_name),
        ).fetchall()
        return [row["doc_id"] for row in rows]

    def lookup_keys(
        self, collection: str, index_name: str, keys: Iterable[bytes]
    ) -> list[str]:
        """Document ids whose index entries match any of ``keys``."""
        found: list[str] = []
        seen: set[str] = set()
        for key in keys:
            cursor = self._connection.execute(
                "SELECT doc_id FROM _minidb_index_entries "
                "WHERE collection = ? AND index_name = ? AND key = ?",
                (collection, index_name, key),
            )
            for row in cursor:
                if row["doc_id"] not in seen:
                    seen.add(row["doc_id"])
                    found.append(row["doc_id"])
        for doc_id in self._overflow_ids(collection, index_name):
            if doc_id not in seen:
                seen.add(doc_id)
                found.append(doc_id)
        return found

    def range_scan(
        self,
        collection: str,
        index_name: str,
        lower: bytes | None,
        lower_inclusive: bool,
        upper: bytes | None,
        upper_inclusive: bool,
    ) -> list[str]:
        """Document ids whose index key falls inside the given range."""
        clauses = ["collection = ?", "index_name = ?"]
        params: list[Any] = [collection, index_name]

        if lower is not None:
            clauses.append(f"key {'>=' if lower_inclusive else '>'} ?")
            params.append(lower)
        if upper is not None:
            clauses.append(f"key {'<=' if upper_inclusive else '<'} ?")
            params.append(upper)

        sql = (
            "SELECT DISTINCT doc_id FROM _minidb_index_entries WHERE "
            + " AND ".join(clauses)
        )
        cursor = self._connection.execute(sql, params)
        found = [row["doc_id"] for row in cursor]

        seen = set(found)
        for doc_id in self._overflow_ids(collection, index_name):
            if doc_id not in seen:
                seen.add(doc_id)
                found.append(doc_id)
        return found

    # -- helpers ------------------------------------------------------

    def key_for(self, value: Any) -> bytes:
        return encode_key(value)
