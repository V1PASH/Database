"""A MongoDB-compatible ObjectId.

Layout (12 bytes, exactly like BSON's ObjectId):

    4 bytes  seconds since the Unix epoch (big endian)
    5 bytes  random value, generated once per process
    3 bytes  counter, incremented for every id created in this process

The random bytes make ids from different machines unlikely to collide; the
counter guarantees uniqueness inside a single process even when several ids
are created in the same second.
"""

from __future__ import annotations

import binascii
import os
import struct
import threading
import time
from datetime import datetime, timezone

__all__ = ["ObjectId"]


class ObjectId:
    """A 12-byte, practically unique identifier."""

    __slots__ = ("__id",)

    _random = os.urandom(5)
    _lock = threading.Lock()
    _counter = int.from_bytes(os.urandom(3), "big")

    def __init__(self, oid: str | bytes | ObjectId | None = None) -> None:
        if oid is None:
            self.__id = self._generate()
        elif isinstance(oid, ObjectId):
            self.__id = oid.binary
        elif isinstance(oid, bytes):
            if len(oid) != 12:
                raise ValueError("ObjectId binary value must be exactly 12 bytes")
            self.__id = oid
        elif isinstance(oid, str):
            if len(oid) != 24:
                raise ValueError("ObjectId string value must be exactly 24 hex characters")
            try:
                self.__id = binascii.unhexlify(oid)
            except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensive
                raise ValueError(f"invalid ObjectId {oid!r}") from exc
        else:
            raise TypeError(f"cannot build an ObjectId from {type(oid).__name__}")

    # -- generation ---------------------------------------------------

    @classmethod
    def _generate(cls) -> bytes:
        with cls._lock:
            counter = cls._counter = (cls._counter + 1) % 0x1000000
        return (
            struct.pack(">I", int(time.time()))
            + cls._random
            + counter.to_bytes(3, "big")
        )

    # -- accessors ----------------------------------------------------

    @property
    def binary(self) -> bytes:
        return self.__id

    @property
    def generation_time(self) -> datetime:
        """The timestamp embedded in the id (UTC)."""
        seconds = struct.unpack(">I", self.__id[:4])[0]
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    # -- dunder -------------------------------------------------------

    def __str__(self) -> str:
        return binascii.hexlify(self.__id).decode("ascii")

    def __repr__(self) -> str:
        return f"ObjectId('{self}')"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ObjectId):
            return self.__id == other.binary
        if isinstance(other, str):
            try:
                return self.__id == ObjectId(other).binary
            except ValueError:
                return False
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.__id)

    def __lt__(self, other: object) -> bool:
        if isinstance(other, ObjectId):
            return self.__id < other.binary
        return NotImplemented

    def __bool__(self) -> bool:
        return True
