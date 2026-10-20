"""Indexes.

minidb keeps its own index structures (rather than leaning on SQLite's) so
that the index semantics match MongoDB's exactly:

* a **multikey** index stores one entry per array element, so a lookup for
  ``{"tags": "python"}`` finds documents whose ``tags`` array contains
  ``"python"``;
* a unique index rejects duplicate values;
* the key encoding is **order preserving**, so the same index can serve
  equality lookups *and* range scans (``$gt``/``$lt``).

The encoding maps a value onto bytes whose byte order matches MongoDB's BSON
comparison order, which is what makes range scans correct.

**Compound indexes.** An index may cover several fields (``[("a", 1),
("b", -1)]``). Its key is the concatenation of the *component* keys, each
one self-delimiting so that the components can be compared one after the
other -- exactly like MongoDB's compound index keys, which are compared
component by component.
"""

from __future__ import annotations

import datetime as _dt
import json
import struct
from typing import Any, NamedTuple

from .bson import dumps as encode_document
from .comparison import (
    RANK_ARRAY,
    RANK_BINARY,
    RANK_BOOL,
    RANK_DATE,
    RANK_NULL,
    RANK_NUMBER,
    RANK_OBJECT,
    RANK_OBJECTID,
    RANK_STRING,
    type_rank,
)
from .errors import WriteError
from .query import get_path

__all__ = [
    "encode_key",
    "index_entries_for_paths",
    "query_candidates",
    "IndexPlan",
    "plan_query",
    "MAX_COMBINATIONS",
]

# A component key ends with this terminator. The payload escapes 0x00 as
# 0x00 0xff, so a 0x00 byte is always followed by 0xff inside a payload and
# the terminator can never be confused with payload content. That is what
# makes each component self-delimiting, and therefore what makes compound
# keys comparable component by component.
TERMINATOR = b"\x00\x00"

# A document that would need more combinations than this is not fully
# indexed; it is recorded in the overflow table instead, so that queries
# still find it (they simply cannot use the index to rule it out).
MAX_COMBINATIONS = 1024

# Bounds used for the unbounded ends of a range scan.
MIN_BOUND = b"\x00" * 16
MAX_BOUND = b"\xff" * 16


def _sortable_double(value: float) -> bytes:
    """Encode a float so that byte order equals numeric order.

    Negative numbers have the high bit set in IEEE-754, which would make them
    sort *after* positive ones. Flipping the bits of negative values (and
    setting the high bit of positive ones) restores the correct order.
    """
    bits = struct.unpack(">Q", struct.pack(">d", float(value)))[0]
    if bits & 0x8000000000000000:
        bits = (~bits) & 0xFFFFFFFFFFFFFFFF
    else:
        bits |= 0x8000000000000000
    return struct.pack(">Q", bits)


def _canonical(value: Any) -> bytes:
    """A stable byte representation for documents and arrays."""
    return json.dumps(
        encode_document(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _escape(payload: bytes) -> bytes:
    """Escape 0x00 bytes so that the terminator stays unambiguous."""
    if b"\x00" not in payload:
        return payload
    return payload.replace(b"\x00", b"\x00\xff")


def encode_key(value: Any) -> bytes:
    """Encode a value into an order-preserving index key.

    The result is one *component* of a key. Concatenating components gives
    a compound key whose byte order compares the components in turn.
    """
    rank = type_rank(value)

    if rank == RANK_NULL:
        payload = b""
    elif rank == RANK_NUMBER:
        payload = _sortable_double(value)
    elif rank == RANK_STRING:
        payload = value.encode("utf-8")
    elif rank == RANK_BOOL:
        payload = b"\x01" if value else b"\x00"
    elif rank == RANK_DATE:
        moment = (
            value
            if isinstance(value, _dt.datetime)
            else _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
        )
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_dt.timezone.utc)
        payload = _sortable_double(moment.timestamp())
    elif rank == RANK_OBJECTID:
        payload = bytes.fromhex(str(value))
    elif rank == RANK_BINARY:
        payload = bytes(value)
    elif rank == RANK_OBJECT:
        payload = _canonical(value)
    elif rank == RANK_ARRAY:
        payload = _canonical(value)
    else:
        raise WriteError(f"values of type {type(value).__name__} cannot be indexed")

    return bytes([rank]) + _escape(payload) + TERMINATOR


def query_candidates(value: Any) -> list[Any]:
    """The values a query looks for in an index.

    A list query value matches documents whose array *contains* those
    elements, so every element is looked up individually. The whole list is
    looked up too, so documents storing exactly that array are found as well.
    """
    if isinstance(value, (list, tuple)):
        items = list(value)
        candidates: list[Any] = [items]
        for item in items:
            candidates.extend(query_candidates(item))
        return candidates
    return [value]


def _candidate_values_for_path(document: Any, path: str) -> list[Any]:
    """The values a document contributes for one component of an index."""
    values, found = get_path(document, path)
    if not found:
        return []

    candidates: list[Any] = []
    seen: set[bytes] = set()

    def add(candidate: Any) -> None:
        try:
            key = encode_key(candidate)
        except WriteError:
            return
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    for value in values:
        add(value)
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
    return candidates


def index_entries_for_paths(document: Any, paths: list[str]) -> tuple[list[bytes], bool]:
    """All index keys a document contributes for a (possibly compound) index.

    Returns ``(keys, overflow)``. ``overflow`` is True when the document
    needs more key combinations than :data:`MAX_COMBINATIONS`; the caller
    then records the document in the overflow table, because an index can
    only ever *narrow* a search -- it must never hide a document.
    """
    per_component: list[list[Any]] = []
    for path in paths:
        candidates = _candidate_values_for_path(document, path)
        if not candidates:
            return [], False
        per_component.append(candidates)

    combinations: list[list[Any]] = [[]]
    for candidates in per_component:
        combinations = [
            combination + [candidate]
            for combination in combinations
            for candidate in candidates
        ]
        if len(combinations) > MAX_COMBINATIONS:
            # Too many combinations to index fully: keep the first one and
            # let the overflow table make sure the document is still found.
            return (
                [b"".join(encode_key(value) for value in combinations[0])],
                True,
            )

    return [
        b"".join(encode_key(value) for value in combination)
        for combination in combinations
    ], False


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


class IndexPlan(NamedTuple):
    """A decision about how to answer a query using an index.

    Every plan describes a condition that is *implied* by the filter, so
    the documents it returns are always a superset of the documents that
    match. The full filter is applied afterwards, which is what keeps the
    answer correct.
    """

    index_name: str
    paths: tuple[str, ...]
    kind: str  # "eq" | "range" | "prefix"
    keys: tuple[bytes, ...] = ()
    lower: bytes | None = None
    lower_inclusive: bool = True
    upper: bytes | None = None
    upper_inclusive: bool = True


def _simple_conditions(query: Any) -> dict[str, dict[str, Any]]:
    """Pull the simple, top-level conditions out of a filter.

    Only conditions that live directly at the top level of the filter are
    considered; anything nested inside ``$or``/``$and`` is left to the
    matcher because the index would have to be combined in a more complex
    way.
    """
    if not isinstance(query, dict):
        return {}

    conditions: dict[str, dict[str, Any]] = {}
    for key, value in query.items():
        if not isinstance(key, str) or key.startswith("$"):
            continue
        if isinstance(value, dict) and value and all(
            isinstance(name, str) and name.startswith("$") for name in value
        ):
            for operator, operand in value.items():
                if operator == "$options":
                    continue
                conditions.setdefault(key, {})[operator] = operand
        else:
            conditions.setdefault(key, {})["$eq"] = value
    return conditions


def _equality_candidates(condition: dict[str, Any]) -> list[Any] | None:
    """The values an equality-ish condition looks for, or None."""
    if "$eq" in condition:
        return query_candidates(condition["$eq"])
    if "$in" in condition and isinstance(condition["$in"], (list, tuple)):
        candidates: list[Any] = []
        for value in condition["$in"]:
            candidates.extend(query_candidates(value))
        return candidates
    return None


def _range_operator(condition: dict[str, Any]) -> str | None:
    """Which comparison operator a condition carries, if any."""
    for operator in ("$gt", "$gte", "$lt", "$lte"):
        if operator in condition:
            return operator
    return None


def _plan_for_index(
    name: str, paths: tuple[str, ...], conditions: dict[str, dict[str, Any]]
) -> IndexPlan | None:
    """Work out how one index could answer the query."""
    prefix: list[bytes] = []
    prefix_values: list[Any] = []

    for position, path in enumerate(paths):
        condition = conditions.get(path)
        if condition is None:
            break

        candidates = _equality_candidates(condition)
        if candidates is not None:
            if len(candidates) == 1:
                prefix.append(encode_key(candidates[0]))
                prefix_values.append(candidates[0])
                continue
            # Several candidates: an exact lookup can use them, but the
            # prefix has to stop here.
            if position == len(paths) - 1:
                keys = tuple(
                    b"".join(encode_key(value) for value in prefix_values + [candidate])
                    for candidate in candidates
                )
                return IndexPlan(name, paths, "eq", keys=keys)
            break

        operator = _range_operator(condition)
        if operator is not None and position == len(prefix_values):
            # The comparison sits directly after the equality prefix, so the
            # whole thing is one contiguous range of compound keys.
            operand = condition[operator]
            prefix_bytes = b"".join(prefix)
            component = encode_key(operand)
            lower = upper = None
            lower_inclusive = upper_inclusive = True
            if operator == "$gt":
                lower, lower_inclusive = prefix_bytes + component + b"\x00", False
            elif operator == "$gte":
                lower, lower_inclusive = prefix_bytes + component, True
            elif operator == "$lt":
                upper, upper_inclusive = prefix_bytes + component, False
            elif operator == "$lte":
                upper, upper_inclusive = prefix_bytes + component + b"\xff", True
            return IndexPlan(
                name,
                paths,
                "range",
                lower=lower,
                lower_inclusive=lower_inclusive,
                upper=upper,
                upper_inclusive=upper_inclusive,
            )
        break

    if len(prefix) == len(paths) and paths:
        # Every component is pinned: an exact key lookup.
        return IndexPlan(name, paths, "eq", keys=(b"".join(prefix),))

    if prefix:
        # Only the first components are pinned: scan everything with that
        # prefix.
        prefix_bytes = b"".join(prefix)
        return IndexPlan(
            name,
            paths,
            "prefix",
            lower=prefix_bytes,
            lower_inclusive=True,
            upper=prefix_bytes + b"\xff",
            upper_inclusive=True,
        )

    return None


def plan_query(query: Any, indexes: list[tuple[str, tuple[str, ...]]]) -> list[IndexPlan]:
    """Suggest index plans for a query.

    ``indexes`` is a list of ``(index_name, indexed_paths)`` pairs. The
    returned plans are ordered by expected selectivity; the engine tries
    them in turn and falls back to a full scan.
    """
    conditions = _simple_conditions(query)
    plans: list[IndexPlan] = []

    for name, paths in indexes:
        try:
            plan = _plan_for_index(name, paths, conditions)
        except WriteError:
            continue
        if plan is not None:
            plans.append(plan)

    # Exact lookups are the most selective, then prefix scans, then ranges.
    plans.sort(key=lambda plan: {"eq": 0, "prefix": 1, "range": 2}[plan.kind])
    return plans
