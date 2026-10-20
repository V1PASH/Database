"""Value comparison rules.

MongoDB compares values of different types using a fixed *type order* rather
than raising an error. That is why ``{"created": {"$gt": 5}}`` also matches a
document whose ``created`` field is the string ``"abc"`` -- strings sort after
numbers. Reproducing that ordering is what makes this database behave like
MongoDB instead of like a naive JSON filter.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal
from typing import Any

from .objectid import ObjectId

__all__ = [
    "type_rank",
    "type_name",
    "compare",
    "values_equal",
    "is_number",
    "is_array",
    "is_document",
]


# Type ranks, lowest to highest. Matches MongoDB's BSON comparison order.
RANK_MINKEY = 0
RANK_NULL = 1
RANK_NUMBER = 2
RANK_STRING = 3
RANK_OBJECT = 4
RANK_ARRAY = 5
RANK_BINARY = 6
RANK_OBJECTID = 7
RANK_BOOL = 8
RANK_DATE = 9
RANK_TIMESTAMP = 10
RANK_REGEX = 11
RANK_MAXKEY = 12


class MinKey:
    """Sentinel that sorts before every other value."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MinKey()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MinKey)

    def __hash__(self) -> int:
        return hash("minidb.MinKey")


class MaxKey:
    """Sentinel that sorts after every other value."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MaxKey()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MaxKey)

    def __hash__(self) -> int:
        return hash("maxidb.MaxKey")


MIN_KEY = MinKey()
MAX_KEY = MaxKey()


def is_number(value: Any) -> bool:
    """True for ints/floats/Decimals, but *not* for booleans."""
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def is_array(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def is_document(value: Any) -> bool:
    return isinstance(value, dict)


def type_rank(value: Any) -> int:
    """Return the comparison rank of a value's type."""
    if isinstance(value, MinKey):
        return RANK_MINKEY
    if value is None:
        return RANK_NULL
    if is_number(value):
        return RANK_NUMBER
    if isinstance(value, str):
        return RANK_STRING
    if isinstance(value, dict):
        return RANK_OBJECT
    if is_array(value):
        return RANK_ARRAY
    if isinstance(value, (bytes, bytearray, memoryview)):
        return RANK_BINARY
    if isinstance(value, ObjectId):
        return RANK_OBJECTID
    if isinstance(value, bool):
        return RANK_BOOL
    if isinstance(value, _dt.datetime):
        return RANK_DATE
    if isinstance(value, _dt.date):
        return RANK_DATE
    if isinstance(value, re.Pattern):
        return RANK_REGEX
    return RANK_MAXKEY


def type_name(value: Any) -> str:
    """A MongoDB-style name for the type of a value (for ``$type``)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if is_array(value):
        return "array"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "binData"
    if isinstance(value, ObjectId):
        return "objectId"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "date"
    if isinstance(value, MinKey):
        return "minKey"
    if isinstance(value, MaxKey):
        return "maxKey"
    return "object"


def _compare_numbers(left: Any, right: Any) -> int:
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        left = Decimal(str(left)) if not isinstance(left, Decimal) else left
        right = Decimal(str(right)) if not isinstance(right, Decimal) else right
        return (left > right) - (left < right)
    if isinstance(left, float) or isinstance(right, float):
        left = float(left)
        right = float(right)
    return (left > right) - (left < right)


def _compare_documents(left: dict, right: dict) -> int:
    """Compare two documents field by field, like BSON does."""
    left_keys = list(left.keys())
    right_keys = list(right.keys())
    for key in left_keys:
        if key not in right:
            return 1
        result = compare(left[key], right[key])
        if result != 0:
            return result
    for key in right_keys:
        if key not in left:
            return -1
    return 0


def _compare_arrays(left: list, right: list) -> int:
    for left_item, right_item in zip(left, right):
        result = compare(left_item, right_item)
        if result != 0:
            return result
    return (len(left) > len(right)) - (len(left) < len(right))


def _normalise_datetime(value: Any) -> Any:
    """Make naive datetimes comparable with aware ones."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_dt.timezone.utc)
        return value
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    return value


def compare(left: Any, right: Any) -> int:
    """Three-way comparison of two values using MongoDB's ordering.

    Returns -1, 0 or 1. Values of different types are ordered by
    :func:`type_rank` rather than raising.
    """
    left_rank = type_rank(left)
    right_rank = type_rank(right)
    if left_rank != right_rank:
        return -1 if left_rank < right_rank else 1

    if left_rank == RANK_NUMBER:
        return _compare_numbers(left, right)
    if left_rank == RANK_STRING:
        return (left > right) - (left < right)
    if left_rank == RANK_BOOL:
        return (int(left) > int(right)) - (int(left) < int(right))
    if left_rank == RANK_OBJECT:
        return _compare_documents(left, right)
    if left_rank == RANK_ARRAY:
        return _compare_arrays(list(left), list(right))
    if left_rank == RANK_BINARY:
        return (bytes(left) > bytes(right)) - (bytes(left) < bytes(right))
    if left_rank == RANK_OBJECTID:
        return (str(left) > str(right)) - (str(left) < str(right))
    if left_rank == RANK_DATE:
        left_dt = _normalise_datetime(left)
        right_dt = _normalise_datetime(right)
        return (left_dt > right_dt) - (left_dt < right_dt)
    if left_rank == RANK_NULL:
        return 0
    return 0


class SortKey:
    """Wraps a value so lists of mixed types can be sorted MongoDB-style."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __lt__(self, other: "SortKey") -> bool:
        return compare(self.value, other.value) < 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SortKey) and compare(self.value, other.value) == 0

    def __hash__(self) -> int:
        return hash(str(self.value))


def values_equal(left: Any, right: Any) -> bool:
    """Equality with MongoDB's type bracketing rules.

    * numbers compare across int/float/Decimal
    * booleans never equal numbers (``True != 1``)
    * documents compare by content, ignoring key order
    """
    if left is None or right is None:
        return left is None and right is None

    left_is_number = is_number(left)
    right_is_number = is_number(right)
    if left_is_number or right_is_number:
        if not (left_is_number and right_is_number):
            return False
        return _compare_numbers(left, right) == 0

    if isinstance(left, bool) or isinstance(right, bool):
        if not (isinstance(left, bool) and isinstance(right, bool)):
            return False
        return left is right

    if isinstance(left, str) or isinstance(right, str):
        if not (isinstance(left, str) and isinstance(right, str)):
            return False
        return left == right

    if isinstance(left, dict) or isinstance(right, dict):
        if not (isinstance(left, dict) and isinstance(right, dict)):
            return False
        if len(left) != len(right):
            return False
        for key, value in left.items():
            if key not in right:
                return False
            if not values_equal(value, right[key]):
                return False
        return True

    if is_array(left) or is_array(right):
        if not (is_array(left) and is_array(right)):
            return False
        left_list = list(left)
        right_list = list(right)
        if len(left_list) != len(right_list):
            return False
        return all(values_equal(a, b) for a, b in zip(left_list, right_list))

    if isinstance(left, (bytes, bytearray)) or isinstance(right, (bytes, bytearray)):
        if not (isinstance(left, (bytes, bytearray)) and isinstance(right, (bytes, bytearray))):
            return False
        return bytes(left) == bytes(right)

    if isinstance(left, ObjectId) or isinstance(right, ObjectId):
        if not (isinstance(left, ObjectId) and isinstance(right, ObjectId)):
            return False
        return left == right

    if isinstance(left, (_dt.datetime, _dt.date)) or isinstance(right, (_dt.datetime, _dt.date)):
        if not (
            isinstance(left, (_dt.datetime, _dt.date))
            and isinstance(right, (_dt.datetime, _dt.date))
        ):
            return False
        left_dt = _normalise_datetime(left)
        right_dt = _normalise_datetime(right)
        return left_dt == right_dt

    if isinstance(left, Decimal) or isinstance(right, Decimal):
        return left == right

    return left == right
