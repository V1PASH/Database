"""A workspace: many database files, browsable together.

minidb is an embedded database, so there is no server that "holds" the
databases -- each one is simply a file. A workspace is the next best thing:
it finds the database files on this machine and lets the browser treat them
as one tree, the way MongoDB Compass treats a server.

    DATABASES
      app          4 collections
        users
        tasks
      notes         1 collection
        ideas
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from ..database import Database
from ..errors import CollectionNotFound

__all__ = ["Workspace", "DEFAULT_REGISTRY"]

#: Where the workspace remembers the databases it has seen.
DEFAULT_REGISTRY = Path.home() / ".minidb" / "workspace.json"

#: The suffixes that mark a file as a database.
DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".minidb")


class Workspace:
    """A set of database files that can be browsed together."""

    def __init__(
        self,
        paths: list[str | os.PathLike[str]] | None = None,
        registry: Path | None = None,
        use_registry: bool = True,
    ) -> None:
        if registry is not None:
            self._registry: Path | None = Path(registry)
        elif use_registry:
            self._registry = DEFAULT_REGISTRY
        else:
            self._registry = None
        self._explicit = [Path(path).expanduser() for path in (paths or [])]
        self._databases: dict[str, Database] = {}
        self._paths: dict[str, Path] = {}
        self._names: dict[Path, str] = {}

    # -- discovery ----------------------------------------------------

    @property
    def roots(self) -> list[Path]:
        """The directories and files this workspace was pointed at."""
        return list(self._explicit)

    @property
    def registry(self) -> Path | None:
        """Where the workspace remembers the databases it has seen."""
        return self._registry

    def discover(self) -> list[Path]:
        """Every database file this workspace knows about."""
        found: list[Path] = []

        def add(path: Path) -> None:
            resolved = path.resolve()
            if resolved.is_file() and resolved not in found:
                found.append(resolved)

        for path in self._explicit:
            if path.is_dir():
                for candidate in sorted(path.glob("*")):
                    if candidate.suffix in DATABASE_SUFFIXES:
                        add(candidate)
            else:
                add(path)

        for path in self._remembered():
            add(path)

        return found

    def _remembered(self) -> list[Path]:
        """The databases this workspace has seen before."""
        if self._registry is None:
            return []
        try:
            data = json.loads(self._registry.read_text())
        except (OSError, ValueError):
            return []
        entries = data.get("databases") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return []
        paths = []
        for entry in entries:
            candidate = Path(str(entry)).expanduser()
            if candidate.is_file():
                paths.append(candidate)
        return paths

    def remember(self, path: str | os.PathLike[str]) -> None:
        """Add a database to the workspace, and remember it for next time."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"no such database file: {resolved}")

        known = [str(item) for item in self._remembered()]
        if str(resolved) not in known:
            known.append(str(resolved))
            self._write_registry(known)

        self._open(resolved)

    def _write_registry(self, known: list[str]) -> None:
        if self._registry is None:
            return
        try:
            self._registry.parent.mkdir(parents=True, exist_ok=True)
            self._registry.write_text(json.dumps({"databases": known}, indent=2) + "\n")
        except OSError:
            pass

    def forget(self, name: str) -> None:
        """Remove a database from the workspace."""
        path = self._paths.pop(name, None)
        self._databases.pop(name, None)
        if path is None:
            return
        known = [item for item in self._remembered() if str(item) != str(path)]
        self._write_registry(known)

    # -- opening ------------------------------------------------------

    def _name_for(self, path: Path, taken: set[str]) -> str:
        """A readable name for a database file, unique within the workspace."""
        stem = path.name
        for suffix in DATABASE_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if not stem:
            stem = "database"
        if stem not in taken:
            return stem
        # Two files with the same name: use the parent directory too.
        parent = path.parent.name or "root"
        candidate = f"{parent}/{stem}"
        counter = 2
        while candidate in taken:
            candidate = f"{parent}/{stem}-{counter}"
            counter += 1
        return candidate

    def _open(self, path: Path) -> Database:
        resolved = path.resolve()
        existing = self._names.get(resolved)
        if existing is not None and existing in self._databases:
            return self._databases[existing]

        taken = set(self._paths)
        name = self._name_for(resolved, taken)
        database = Database(str(resolved))
        self._databases[name] = database
        self._paths[name] = resolved
        self._names[resolved] = name
        return database

    def databases(self) -> list[dict]:
        """Every database in the workspace, with its collections."""
        found = []
        for path in self.discover():
            name = self._name_for(path, {item["name"] for item in found})
            database = self._open(path)
            found.append(
                {
                    "name": name,
                    "path": str(path),
                    "collections": [
                        {
                            "name": collection,
                            "count": len(database[collection]),
                            "indexes": database[collection].list_indexes(),
                        }
                        for collection in database.list_collection_names()
                    ],
                }
            )
        return found

    def database(self, name: str) -> Database:
        """One database by name."""
        if name in self._databases:
            return self._databases[name]
        for path in self.discover():
            candidate = self._name_for(path, set())
            database = self._open(path)
            if candidate == name or self._names.get(path.resolve()) == name:
                return database
        raise CollectionNotFound(name)

    def close(self) -> None:
        """Close every database in the workspace."""
        for database in self._databases.values():
            try:
                database.close()
            except Exception:  # pragma: no cover - best effort
                pass
        self._databases.clear()
        self._paths.clear()
        self._names.clear()

    def __iter__(self) -> Iterator[dict]:
        return iter(self.databases())

    def __repr__(self) -> str:
        return f"Workspace({len(self._databases)} databases)"
