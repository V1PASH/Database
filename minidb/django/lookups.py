"""Translating Django's lookups into minidb filters.

Django describes a query as Python objects -- ``Q`` objects and lookup
names -- long before it turns into SQL. That is the level worth
translating at: the ORM's own query building is reused, and only the
final "what does the filter mean" step is re-implemented for a document
store.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["build_filter", "merge_filters", "UnsupportedLookup"]


class UnsupportedLookup(Exception):
    """Raised for a lookup that has no minidb equivalent."""


#: Lookups that minidb can express directly.
DIRECT = {
    "exact",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "isnull",
    "range",
}

#: Lookups that become a regular expression.
REGEX = {
    "contains",
    "icontains",
    "startswith",
    "istartswith",
    "endswith",
    "iendswith",
    "iexact",
    "regex",
    "iregex",
}


def _regex_filter(value: str, prefix: str = "", suffix: str = "", ignore_case: bool = False) -> dict:
    """A filter that matches a string by pattern."""
    pattern = prefix + re.escape(str(value)) + suffix
    expression: dict[str, Any] = {"$regex": pattern}
    if ignore_case:
        expression["$options"] = "i"
    return expression


def lookup_filter(path: str, lookup: str, value: Any) -> dict:
    """Turn one Django lookup into a minidb filter."""
    if lookup in ("exact", ""):
        return {path: value}

    if lookup in ("gt", "gte", "lt", "lte"):
        return {path: {f"${lookup}": value}}

    if lookup == "in":
        return {path: {"$in": list(value)}}

    if lookup == "isnull":
        return {path: {"$eq": None} if value else {"$ne": None}}

    if lookup == "range":
        low, high = value
        return {path: {"$gte": low, "$lte": high}}

    if lookup in ("contains", "icontains"):
        return {path: _regex_filter(value, ignore_case=lookup.startswith("i"))}

    if lookup in ("startswith", "istartswith"):
        return {path: _regex_filter(value, prefix="^", ignore_case=lookup.startswith("i"))}

    if lookup in ("endswith", "iendswith"):
        return {path: _regex_filter(value, suffix="$", ignore_case=lookup.startswith("i"))}

    if lookup in ("iexact",):
        return {path: _regex_filter(value, prefix="^", suffix="$", ignore_case=True)}

    if lookup in ("regex", "iregex"):
        expression: dict[str, Any] = {"$regex": value}
        if lookup == "iregex":
            expression["$options"] = "i"
        return {path: expression}

    raise UnsupportedLookup(f"the {lookup!r} lookup is not supported by minidb")


def _split_lookup(key: str) -> tuple[str, str]:
    """``"profile__name__icontains"`` -> ``("profile.name", "icontains")``."""
    parts = key.split("__")
    if len(parts) == 1:
        return parts[0], "exact"
    return ".".join(parts[:-1]), parts[-1]


def _apply_alias(path: str, aliases: dict[str, str]) -> str:
    """Rename the first segment of a path, if it is an alias."""
    if not aliases:
        return path
    first, _, rest = path.partition(".")
    renamed = aliases.get(first, first)
    return renamed + ("." + rest if rest else "")


def merge_filters(left: dict, right: dict) -> dict:
    """Combine two filters, the way ``AND`` would."""
    if not left:
        return dict(right)
    if not right:
        return dict(left)

    merged = dict(left)
    for key, value in right.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            combined = dict(merged[key])
            for operator, operand in value.items():
                if operator in combined and isinstance(combined[operator], dict):
                    combined[operator] = {**combined[operator], **operand}
                else:
                    combined[operator] = operand
            merged[key] = combined
        elif key in merged:
            merged[key] = {"$and": [merged[key], value]}
        else:
            merged[key] = value
    return merged


def _from_q(node: Any, aliases: dict[str, str] | None = None) -> dict:
    """Turn a Django ``Q`` object into a minidb filter."""
    children = list(node.children)
    if not children:
        return {}

    parts: list[dict] = []
    for child in children:
        if hasattr(child, "children"):
            parts.append(_from_q(child, aliases))
        else:
            key, value = child
            path, lookup = _split_lookup(key)
            parts.append(lookup_filter(_apply_alias(path, aliases or {}), lookup, value))

    if node.connector == "OR":
        if len(parts) == 1:
            return parts[0]
        return {"$or": parts}
    if node.connector == "AND":
        merged: dict = {}
        for part in parts:
            merged = merge_filters(merged, part)
        return merged
    if node.connector == "NOR":
        return {"$nor": parts}
    raise UnsupportedLookup(f"unsupported Q connector: {node.connector!r}")


def build_filter(args: tuple, kwargs: dict, aliases: dict[str, str] | None = None) -> dict:
    """Build a minidb filter from Django's ``filter(*args, **kwargs)``.

    ``aliases`` renames the first segment of a path -- Django's ``pk`` and
    the model's own primary key field are stored as the document's ``_id``.
    """
    parts: list[dict] = []

    for node in args:
        if hasattr(node, "children"):
            parts.append(_from_q(node, aliases))
        else:
            raise UnsupportedLookup(f"cannot use {node!r} as a filter")

    for key, value in kwargs.items():
        path, lookup = _split_lookup(key)
        parts.append(lookup_filter(_apply_alias(path, aliases or {}), lookup, value))

    merged: dict = {}
    for part in parts:
        merged = merge_filters(merged, part)
    return merged
