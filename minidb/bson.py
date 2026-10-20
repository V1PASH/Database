"""Document codec.

Documents are stored as JSON text. A handful of extended types are supported
by tagging them in the JSON, the same way BSON does it:

    ObjectId   -> {"$oid": "66a1b2c3d4e5f60718293a4b"}
    datetime   -> {"$date": "2026-10-20T12:00:00+00:00"}
    date       -> {"$date": "2026-10-20T00:00:00"}
    time       -> {"$time": "12:00:00"}
    UUID       -> {"$uuid": "0f1e2d3c-..."}
    Decimal    -> {"$numberDecimal": "12.34"}
    bytes      -> {"$binary": "aGVsbG8="}

Decoded documents are returned as :class:`Document` (a ``dict`` subclass). That
marker is what lets us tell "this dict came out of the database" apart from
"this dict was written by the user", so a literal ``{"$oid": ...}`` in user
code is not silently rewritten on the way in and back out.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import math
import uuid
from decimal import Decimal
from typing import Any

from .errors import InvalidDocument
from .objectid import ObjectId

__all__ = [
    "Document",
    "dumps",
    "loads",
    "encode_value",
    "decode_value",
    "to_jsonable",
]


class Document(dict):
    """A dict that came out of the database.

    Behaves exactly like a normal dict; the subclass exists only as a marker
    so that tagged values (``{"$date": ...}`` and friends) are decoded on
    read but left alone when they are part of a user-supplied plain dict.
    """

    __slots__ = ()


def _tag(value: Any) -> Any:
    """Convert one value into something ``json.dumps`` can handle."""
    if isinstance(value, ObjectId):
        return {"$oid": str(value)}
    if isinstance(value, _dt.datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, _dt.date):
        return {"$date": _dt.datetime(value.year, value.month, value.day).isoformat()}
    if isinstance(value, _dt.time):
        return {"$time": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, Decimal):
        return {"$numberDecimal": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$binary": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (set, frozenset)):
        return {"$set": [_tag(item) for item in value]}
    if isinstance(value, tuple):
        return [_tag(item) for item in value]
    if isinstance(value, list):
        return [_tag(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _tag(item) for key, item in value.items()}
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise InvalidDocument(f"cannot store non-finite number {value!r}")
    return value


def _parse_date(text: str) -> _dt.datetime | _dt.date:
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidDocument(f"invalid date value {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _object_hook(raw: dict) -> Any:
    """Decode tagged values. Called by :func:`json.loads` for every object."""
    if len(raw) == 1:
        if "$oid" in raw:
            return ObjectId(raw["$oid"])
        if "$date" in raw:
            return _parse_date(raw["$date"])
        if "$time" in raw:
            return _dt.time.fromisoformat(raw["$time"])
        if "$uuid" in raw:
            return uuid.UUID(raw["$uuid"])
        if "$numberDecimal" in raw:
            return Decimal(raw["$numberDecimal"])
        if "$binary" in raw:
            return base64.b64decode(raw["$binary"])
        if "$set" in raw:
            return {_tag_to_python(item) for item in raw["$set"]}
    return Document(raw)


def _tag_to_python(value: Any) -> Any:
    if isinstance(value, dict):
        decoded = _object_hook(value)
        return decoded
    return value


def dumps(document: Any) -> str:
    """Serialise a document to the JSON text that gets stored on disk."""
    try:
        return json.dumps(
            _tag(document),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=False,
        )
    except InvalidDocument:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidDocument(f"cannot encode document: {exc}") from exc


def loads(text: str) -> Document:
    """Deserialise stored JSON text back into a :class:`Document`."""
    try:
        decoded = json.loads(text, object_hook=_object_hook)
    except (TypeError, ValueError) as exc:
        raise InvalidDocument(f"cannot decode document: {exc}") from exc
    if not isinstance(decoded, dict):
        raise InvalidDocument("stored document is not an object")
    return decoded


def encode_value(value: Any) -> Any:
    """Public helper: turn an extended-typed value into a JSON-ready value."""
    return _tag(value)


def decode_value(value: Any) -> Any:
    """Public helper: turn a JSON-ready value back into extended types."""
    if isinstance(value, dict):
        return _object_hook(value)
    if isinstance(value, list):
        return [_tag_to_python(item) for item in value]
    return value


def to_jsonable(value: Any) -> Any:
    """Make a stored value safe to hand to a JSON encoder.

    The extended types are written the way MongoDB's Extended JSON writes
    them, so the output is familiar to anyone who has used a MongoDB shell::

        ObjectId("...")  ->  {"$oid": "..."}
        datetime         ->  {"$date": "2026-10-20T12:00:00+00:00"}
        Decimal("1.5")   ->  {"$numberDecimal": "1.5"}
        b"hi"            ->  {"$binary": "aGk="}
    """
    import base64 as _base64
    import datetime as _datetime
    import uuid as _uuid
    from decimal import Decimal as _Decimal

    from .objectid import ObjectId as _ObjectId

    if isinstance(value, _ObjectId):
        return {"$oid": str(value)}
    if isinstance(value, _datetime.datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, _datetime.date):
        return {"$date": value.isoformat()}
    if isinstance(value, _uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, _Decimal):
        return {"$numberDecimal": str(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"$binary": _base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
