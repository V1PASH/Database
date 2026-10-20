"""Inferring a schema from the data that is actually there.

A validator says what a document *should* look like. This module says what
the stored documents *do* look like, which is what a database browser shows
you: the fields that appear, the types they hold, and which of them are
sometimes missing.

    >>> infer_schema([{"name": "Ada", "age": 36}, {"name": "Alan"}])
    [{'name': 'age', 'types': ['integer'], 'count': 1, 'optional': True}, ...]
"""

from __future__ import annotations

from typing import Any, Iterable

from .comparison import is_array, is_document, type_name

__all__ = ["infer_schema"]


def _walk(
    value: Any,
    prefix: str,
    seen: set[str],
    fields: dict[str, dict[str, Any]],
) -> None:
    """Record the type of every field found under ``value``."""
    if is_document(value):
        items = value.items()
    elif is_array(value):
        # An array of documents contributes the shape of its members.
        items = []
        for element in value:
            if is_document(element):
                items.extend(element.items())
    else:
        return

    for key, item in items:
        path = f"{prefix}.{key}" if prefix else str(key)
        seen.add(path)

        info = fields.setdefault(path, {"types": set(), "count": 0})
        info["types"].add(type_name(item))

        if is_document(item) or is_array(item):
            _walk(item, path, seen, fields)


def infer_schema(documents: Iterable[dict], sample: int = 200) -> list[dict]:
    """Describe the fields found in a sample of documents.

    Returns one entry per field path, sorted by name.
    """
    documents = list(documents)[:sample]
    total = len(documents)

    fields: dict[str, dict[str, Any]] = {}
    for document in documents:
        seen: set[str] = set()
        _walk(document, "", seen, fields)
        for path in seen:
            fields[path]["count"] += 1

    return [
        {
            "name": path,
            "types": sorted(info["types"]),
            "count": info["count"],
            "optional": info["count"] < total,
        }
        for path, info in sorted(fields.items())
    ]
