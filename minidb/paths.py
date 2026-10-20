"""Helpers for reading and writing dotted paths inside documents.

Used by the update operators, projections and the aggregation pipeline.
"""

from __future__ import annotations

from typing import Any

from .errors import WriteError
from .query import get_path, split_path

__all__ = ["set_path", "unset_path", "get_path", "split_path", "rename_path"]


def _container_get(container: Any, key: str) -> Any:
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, list) and key.isdigit():
        index = int(key)
        if index < len(container):
            return container[index]
    return None


def _container_set(container: Any, key: str, value: Any) -> None:
    if isinstance(container, dict):
        container[key] = value
        return
    if isinstance(container, list) and key.isdigit():
        index = int(key)
        while len(container) <= index:
            container.append(None)
        container[index] = value
        return
    raise WriteError(f"cannot set {key!r} on a {type(container).__name__}")


def set_path(document: Any, path: str, value: Any) -> None:
    """Set ``path`` to ``value``, creating intermediate containers as needed."""
    parts = split_path(path)
    _set_parts(document, parts, value)


def _set_parts(container: Any, parts: list[str], value: Any) -> None:
    key = parts[0]
    if len(parts) == 1:
        _container_set(container, key, value)
        return

    child = _container_get(container, key)
    if not isinstance(child, (dict, list)):
        # Create the container the next segment needs: a list if the next
        # segment is an index, otherwise a document.
        child = [] if parts[1].isdigit() else {}
        _container_set(container, key, child)
    _set_parts(child, parts[1:], value)


def unset_path(document: Any, path: str) -> bool:
    """Remove ``path``. Returns True when something was actually removed."""
    parts = split_path(path)
    container: Any = document
    for part in parts[:-1]:
        child = _container_get(container, part)
        if not isinstance(child, (dict, list)):
            return False
        container = child

    last = parts[-1]
    if isinstance(container, dict):
        if last in container:
            del container[last]
            return True
        return False
    if isinstance(container, list) and last.isdigit():
        index = int(last)
        if index < len(container):
            # MongoDB nulls out array members rather than shrinking the array.
            container[index] = None
            return True
    return False


def rename_path(document: Any, path: str, new_path: str) -> bool:
    """Move the value at ``path`` to ``new_path``."""
    values, found = get_path(document, path)
    if not found:
        return False
    unset_path(document, path)
    set_path(document, new_path, values[0])
    return True
