"""Exception hierarchy for minidb.

The names mirror pymongo's where it makes sense, so that code written against
this database can be ported to (or from) MongoDB with minimal friction.
"""

from __future__ import annotations


class MiniDBError(Exception):
    """Base class for every error raised by minidb."""


class ConfigurationError(MiniDBError):
    """Raised when the database is configured or opened incorrectly."""


class CollectionNotFound(MiniDBError):
    """Raised when a collection is referenced that does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(f"collection {name!r} does not exist")
        self.name = name


class CollectionAlreadyExists(MiniDBError):
    """Raised when creating a collection that already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"collection {name!r} already exists")
        self.name = name


class DocumentNotFound(MiniDBError):
    """Raised when an operation expects a document and none matched."""

    def __init__(self, collection: str, filter: object = None) -> None:
        super().__init__(f"no document matched in {collection!r} (filter={filter!r})")
        self.collection = collection
        self.filter = filter


class DuplicateKeyError(MiniDBError):
    """Raised when a unique index would be violated."""


class WriteError(MiniDBError):
    """Raised when a write cannot be performed (bad operator, type mismatch)."""


class ValidationError(MiniDBError):
    """Raised when a document fails the collection's schema validation."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class InvalidOperation(MiniDBError):
    """Raised when an operation is not valid for the current state (e.g. sort)."""


class InvalidDocument(MiniDBError):
    """Raised when a value cannot be encoded as a document."""


class OperationFailure(MiniDBError):
    """Raised when an operation cannot be carried out on the server."""
