"""minidb -- a MongoDB-style document database written from scratch.

    from minidb import Database

    db = Database("app.db")
    db.create_collection("users")
    db.users.insert_one({"name": "Ada", "role": "engineer"})
    db.users.find_one({"role": "engineer"})

The API deliberately mirrors PyMongo's, so application code can move between
this database and a real MongoDB server with few changes.
"""

from __future__ import annotations

from .aggregation import evaluate_expression, run_pipeline
from .bson import Document
from .collection import (
    Collection,
    Cursor,
    DeleteResult,
    InsertManyResult,
    InsertOneResult,
    ReturnDocument,
    UpdateResult,
)
from .comparison import compare, values_equal
from .database import Database
from .errors import (
    CollectionAlreadyExists,
    CollectionNotFound,
    ConfigurationError,
    DocumentNotFound,
    DuplicateKeyError,
    InvalidDocument,
    InvalidOperation,
    MiniDBError,
    OperationFailure,
    ValidationError,
    WriteError,
)
from .objectid import ObjectId
from .query import match_document
from .update import apply_update
from .validation import Validator

__version__ = "0.7.1"

__all__ = [
    "Database",
    "Collection",
    "Cursor",
    "ObjectId",
    "Document",
    "Validator",
    "ReturnDocument",
    "InsertOneResult",
    "InsertManyResult",
    "UpdateResult",
    "DeleteResult",
    "MiniDBError",
    "ConfigurationError",
    "CollectionNotFound",
    "CollectionAlreadyExists",
    "DocumentNotFound",
    "DuplicateKeyError",
    "WriteError",
    "ValidationError",
    "InvalidOperation",
    "InvalidDocument",
    "OperationFailure",
    "match_document",
    "apply_update",
    "compare",
    "values_equal",
    "evaluate_expression",
    "run_pipeline",
    "__version__",
]
