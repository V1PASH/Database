"""The Collection API.

Deliberately shaped like PyMongo's, so that application code written against
minidb keeps working if you ever point it at a real MongoDB server::

    db.users.insert_one({"name": "Ada"})
    db.users.find_one({"name": "Ada"})
    db.users.find({"age": {"$gte": 30}}, sort=[("age", -1)])
    db.users.update_one({"_id": oid}, {"$set": {"active": True}})
    db.users.create_index("email", unique=True)
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

from .aggregation import run_pipeline
from .comparison import SortKey, is_array, values_equal
from .engine import doc_key
from .errors import CollectionNotFound, DuplicateKeyError, WriteError
from .index import plan_query
from .objectid import ObjectId
from .query import match_document
from .update import apply_update
from .validation import Validator, apply_defaults, validate_document

__all__ = [
    "Collection",
    "Cursor",
    "InsertOneResult",
    "InsertManyResult",
    "UpdateResult",
    "DeleteResult",
    "ReturnDocument",
]


class ReturnDocument:
    BEFORE = False
    AFTER = True


class InsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id

    def __repr__(self) -> str:
        return f"InsertOneResult({self.inserted_id!r})"


class InsertManyResult:
    def __init__(self, inserted_ids: list[Any]) -> None:
        self.inserted_ids = inserted_ids

    def __repr__(self) -> str:
        return f"InsertManyResult({self.inserted_ids!r})"


class UpdateResult:
    def __init__(
        self, matched_count: int = 0, modified_count: int = 0, upserted_id: Any = None
    ) -> None:
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id

    def __repr__(self) -> str:
        return (
            f"UpdateResult(matched_count={self.matched_count}, "
            f"modified_count={self.modified_count}, upserted_id={self.upserted_id!r})"
        )


class DeleteResult:
    def __init__(self, deleted_count: int = 0) -> None:
        self.deleted_count = deleted_count

    def __repr__(self) -> str:
        return f"DeleteResult(deleted_count={self.deleted_count})"


class Cursor:
    """A lazy view over the documents matching a query."""

    def __init__(
        self,
        collection: "Collection",
        filter: Any = None,
        projection: Any = None,
        sort: Any = None,
        skip: int = 0,
        limit: int = 0,
        results: list[dict] | None = None,
    ) -> None:
        self._collection = collection
        self._filter = filter or {}
        self._projection = projection
        self._sort = sort
        self._skip = skip
        self._limit = limit
        self._results = results
        self._closed = False

    # -- chaining (PyMongo style) -------------------------------------

    def sort(self, key_or_list: Any, direction: Any = None) -> "Cursor":
        if isinstance(key_or_list, str):
            self._sort = [(key_or_list, direction if direction is not None else 1)]
        else:
            self._sort = [tuple(item) for item in key_or_list]
        return self

    def limit(self, count: int) -> "Cursor":
        self._limit = count
        return self

    def skip(self, count: int) -> "Cursor":
        self._skip = count
        return self

    def close(self) -> None:
        self._closed = True

    # -- reading -------------------------------------------------------

    def _documents(self) -> list[dict]:
        if self._results is not None:
            documents = list(self._results)
        else:
            documents = self._collection._read(self._filter)

        if self._sort:
            documents = _sort_documents(documents, self._sort)
        if self._skip:
            documents = documents[self._skip:]
        if self._limit:
            documents = documents[: self._limit]
        if self._projection:
            documents = [
                _apply_projection(document, self._projection) for document in documents
            ]
        return documents

    def __iter__(self) -> Iterator[dict]:
        for document in self._documents():
            yield document
        self._closed = True

    def count(self) -> int:
        return sum(1 for _ in self)

    def to_list(self, length: int | None = None) -> list[dict]:
        documents = list(self)
        return documents if length is None else documents[:length]

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, int):
            if index < 0:
                raise IndexError("negative indexes are not supported")
            for position, document in enumerate(self):
                if position == index:
                    return document
            raise IndexError("no such document")
        if isinstance(index, slice):
            start = index.start or 0
            stop = index.stop
            return list(self)[start:stop]
        raise TypeError("cursor indices must be integers or slices")

    def __repr__(self) -> str:
        return f"Cursor({self._collection.name!r}, filter={self._filter!r})"


def _sort_documents(documents: list[dict], sort: Any) -> list[dict]:
    """Sort documents the way MongoDB does."""
    if isinstance(sort, dict):
        pairs = list(sort.items())
    else:
        pairs = [tuple(item) for item in sort]

    ordered = list(documents)
    for field, direction in reversed(pairs):
        ordered.sort(
            key=lambda document, f=field: SortKey(_field_value(document, f)),
            reverse=direction < 0,
        )
    return ordered


def _field_value(document: dict, field: str) -> Any:
    from .query import get_path

    values, _ = get_path(document, field)
    return values[0] if values else None


class Collection:
    """A named collection of documents."""

    def __init__(self, database: Any, name: str) -> None:
        self._database = database
        self._name = name
        self._storage = database._storage

    # -- basics --------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def database(self) -> Any:
        return self._database

    def __repr__(self) -> str:
        return f"Collection({self._database.name!r}, {self._name!r})"

    def __getitem__(self, name: str) -> "Collection":
        return Collection(self._database, name)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.find())

    def __len__(self) -> int:
        return self._storage.count(self._name)

    # -- schema --------------------------------------------------------

    @property
    def validator(self) -> Validator | None:
        raw = self._storage.get_validator(self._name)
        return Validator(raw) if raw else None

    def _write_validator(self) -> Validator | None:
        """The validator to check a write against, if there is one yet.

        A collection is created by its first insert, so a write can arrive
        before the collection exists. In that case there is nothing to
        validate against -- yet.
        """
        try:
            raw = self._storage.get_validator(self._name)
        except CollectionNotFound:
            return None
        return Validator(raw) if raw else None

    def _prepare(self, document: dict, is_insert: bool = True) -> dict:
        """Validate a document and fill in defaults."""
        prepared = dict(document)
        validator = self._write_validator()
        if validator is not None:
            if is_insert:
                apply_defaults(prepared, validator)
            validate_document(prepared, validator)
        return prepared

    # -- reading -------------------------------------------------------

    def _candidate_ids(self, filter: Any) -> list[str] | None:
        """Document ids that could match, using an index when possible.

        Returns ``None`` when no index can help, meaning "scan everything".
        The result is always a *superset* of the matching documents; the full
        filter is applied afterwards, which is what keeps the answers
        correct even when an index is only an approximation.
        """
        if not isinstance(filter, dict):
            return None

        # The _id is the primary key, so it can always be used directly.
        id_value = filter.get("_id")
        if id_value is not None and "$or" not in filter and "$and" not in filter:
            operators = id_value if isinstance(id_value, dict) else {"$eq": id_value}
            if "$eq" in operators:
                return [doc_key(operators["$eq"])]
            if "$in" in operators and isinstance(operators["$in"], (list, tuple)):
                return [doc_key(candidate) for candidate in operators["$in"]]

        indexes = self._storage.index_definitions(self._name)
        plans = plan_query(filter, indexes)
        if not plans:
            return None

        plan = plans[0]
        if plan.kind == "eq":
            return self._storage.lookup_keys(self._name, plan.index_name, plan.keys)
        return self._storage.range_scan(
            self._name,
            plan.index_name,
            plan.lower,
            plan.lower_inclusive,
            plan.upper,
            plan.upper_inclusive,
        )

    def _read(self, filter: Any = None) -> list[dict]:
        """All documents matching ``filter``, in insertion order."""
        candidate_ids = self._candidate_ids(filter)

        if candidate_ids is None:
            documents = [document for _, document in self._storage.scan(self._name)]
        else:
            documents = self._storage.get_many(self._name, candidate_ids)

        if not filter:
            return documents
        return [document for document in documents if match_document(document, filter)]

    def find(
        self,
        filter: Any = None,
        projection: Any = None,
        sort: Any = None,
        skip: int = 0,
        limit: int = 0,
    ) -> Cursor:
        """Return a cursor over the matching documents."""
        return Cursor(self, filter, projection, sort, skip, limit)

    def find_one(
        self,
        filter: Any = None,
        projection: Any = None,
        sort: Any = None,
        skip: int = 0,
    ) -> dict | None:
        """The first matching document, or ``None``."""
        cursor = Cursor(self, filter, projection, sort, skip, 0)
        for document in cursor:
            return document
        return None

    def inferred_schema(self, sample: int = 200) -> list[dict]:
        """The fields this collection's documents actually contain.

        This is inferred from the stored data, so it describes reality
        rather than what the schema says should be there.
        """
        from .schema import infer_schema

        return infer_schema(self._read(None), sample)

    def stats(self, sample: int = 200) -> dict:
        """A summary of the collection: size, indexes and inferred schema."""
        indexes = self.list_indexes()
        return {
            "count": len(self),
            "dataSize": self._storage.data_size(self._name),
            "indexCount": len(indexes),
            "indexes": indexes,
            "schema": self.inferred_schema(sample),
            "validator": self.validator.schema if self.validator else None,
        }

    def explain(self, filter: Any = None) -> dict:
        """Show how a query would be answered.

        Handy when you want to know whether an index is being used::

            db.users.explain({"email": "ada@example.com"})
        """
        indexes = self._storage.index_definitions(self._name)
        plans = plan_query(filter, indexes)
        total = self._storage.count(self._name)

        candidate_ids = self._candidate_ids(filter)
        examined = total if candidate_ids is None else len(candidate_ids)
        matched = self.count_documents(filter)

        if candidate_ids is None:
            strategy = "COLLSCAN"
        elif plans and plans[0].paths == ("_id",):
            strategy = "IDHACK"
        elif plans:
            strategy = "IXSCAN"
        else:
            strategy = "COLLSCAN"

        return {
            "collection": self._name,
            "filter": filter,
            "strategy": strategy,
            "index": plans[0].index_name if plans and candidate_ids is not None else None,
            "indexedFields": list(plans[0].paths) if plans and candidate_ids is not None else None,
            "totalDocuments": total,
            "documentsExamined": examined,
            "documentsMatched": matched,
            "availableIndexes": {name: list(paths) for name, paths in indexes},
        }

    def count_documents(self, filter: Any = None) -> int:
        """How many documents match the filter."""
        if not filter:
            return self._storage.count(self._name)
        return sum(1 for _ in self._read(filter))

    def distinct(self, field: str, filter: Any = None) -> list[Any]:
        """The distinct values of a field across the matching documents."""
        from .query import get_path

        values: list[Any] = []
        for document in self._read(filter):
            found, _ = get_path(document, field)
            for value in found:
                candidates = list(value) if is_array(value) else [value]
                for candidate in candidates:
                    if not any(values_equal(candidate, existing) for existing in values):
                        values.append(candidate)
        return values

    def aggregate(self, pipeline: Sequence[dict]) -> Cursor:
        """Run an aggregation pipeline."""
        documents = self._read(None)
        results = run_pipeline(documents, list(pipeline))
        return Cursor(self, None, None, None, 0, 0, results=results)

    # -- writing -------------------------------------------------------

    def _next_id(self, document: dict) -> Any:
        if "_id" in document:
            return document["_id"]
        return ObjectId()

    def insert_one(self, document: Any) -> InsertOneResult:
        """Insert a single document."""
        if not isinstance(document, dict):
            raise WriteError("a document must be a dictionary")
        prepared = self._prepare(document)
        document_id = self._next_id(prepared)
        prepared["_id"] = document_id

        key = doc_key(document_id)
        if self._storage.get(self._name, key) is not None:
            raise DuplicateKeyError(f"a document with _id {document_id!r} already exists")

        with self._storage.transaction():
            self._storage.insert(self._name, key, prepared)
        return InsertOneResult(document_id)

    def insert(self, document: Any) -> InsertOneResult | InsertManyResult:
        """Insert a document, or a list of documents.

        A convenience alias for :meth:`insert_one` and :meth:`insert_many`,
        which are the explicit names -- and the ones PyMongo uses.
        """
        if isinstance(document, (list, tuple)):
            return self.insert_many(document)
        return self.insert_one(document)

    def insert_many(self, documents: Any) -> InsertManyResult:
        """Insert several documents."""
        if not isinstance(documents, (list, tuple)):
            raise WriteError("insert_many needs a list of documents")

        prepared_documents = []
        for document in documents:
            if not isinstance(document, dict):
                raise WriteError("a document must be a dictionary")
            prepared = self._prepare(document)
            document_id = self._next_id(prepared)
            prepared["_id"] = document_id
            prepared_documents.append((doc_key(document_id), prepared))

        inserted_ids = []
        with self._storage.transaction():
            for key, prepared in prepared_documents:
                if self._storage.get(self._name, key) is not None:
                    raise DuplicateKeyError(
                        f"a document with _id {prepared['_id']!r} already exists"
                    )
                self._storage.insert(self._name, key, prepared)
                inserted_ids.append(prepared["_id"])
        return InsertManyResult(inserted_ids)

    def _upsert_document(self, filter: Any, update: Any) -> dict:
        """Build the document to insert when an upsert finds no match."""
        document: dict = {}
        if isinstance(filter, dict):
            for field, value in filter.items():
                if field.startswith("$"):
                    continue
                if isinstance(value, dict) and any(
                    isinstance(key, str) and key.startswith("$") for key in value
                ):
                    continue
                if isinstance(value, dict):
                    for key, item in value.items():
                        if isinstance(key, str) and key.startswith("$"):
                            continue
                        document.setdefault(field, {})[key] = item
                else:
                    document[field] = value
        if isinstance(update, dict) and "$set" in update:
            for field, value in update["$set"].items():
                _set_nested(document, field, value)
        return document

    def update_one(
        self,
        filter: Any,
        update: Any,
        upsert: bool = False,
        array_filters: Any = None,
    ) -> UpdateResult:
        """Update the first matching document."""
        result = self._update(filter, update, upsert, array_filters, many=False)
        return result

    def update_many(
        self,
        filter: Any,
        update: Any,
        upsert: bool = False,
        array_filters: Any = None,
    ) -> UpdateResult:
        """Update every matching document."""
        return self._update(filter, update, upsert, array_filters, many=True)

    def replace_one(self, filter: Any, replacement: Any, upsert: bool = False) -> UpdateResult:
        """Replace the first matching document."""
        return self._update(filter, replacement, upsert, None, many=False, replace=True)

    def _update(
        self,
        filter: Any,
        update: Any,
        upsert: bool,
        array_filters: Any,
        many: bool,
        replace: bool = False,
    ) -> UpdateResult:
        matches = self._read(filter)

        if not matches:
            if not upsert:
                return UpdateResult()
            document = self._upsert_document(filter, update)
            prepared = self._prepare(document)
            if "_id" not in prepared:
                prepared["_id"] = ObjectId()
            key = doc_key(prepared["_id"])
            with self._storage.transaction():
                self._storage.insert(self._name, key, prepared)
            return UpdateResult(matched_count=0, modified_count=0, upserted_id=prepared["_id"])

        if not many:
            matches = matches[:1]

        modified = 0
        with self._storage.transaction():
            for original in matches:
                updated = dict(original)
                if replace:
                    updated = dict(update)
                    if "_id" in original:
                        updated["_id"] = original["_id"]
                else:
                    apply_update(
                        updated,
                        update,
                        is_insert=False,
                        array_filters=array_filters,
                    )
                updated = self._prepare(updated, is_insert=False)
                key = doc_key(updated["_id"])
                self._storage.replace(self._name, key, updated)
                modified += 1

        return UpdateResult(matched_count=len(matches), modified_count=modified)

    def delete_one(self, filter: Any) -> DeleteResult:
        """Delete the first matching document."""
        return self._delete(filter, many=False)

    def delete_many(self, filter: Any) -> DeleteResult:
        """Delete every matching document."""
        return self._delete(filter, many=True)

    def _delete(self, filter: Any, many: bool) -> DeleteResult:
        matches = self._read(filter)
        if not many:
            matches = matches[:1]
        deleted = 0
        with self._storage.transaction():
            for document in matches:
                self._storage.delete(self._name, doc_key(document["_id"]))
                deleted += 1
        return DeleteResult(deleted_count=deleted)

    # -- find and modify -----------------------------------------------

    def find_one_and_update(
        self,
        filter: Any,
        update: Any,
        projection: Any = None,
        sort: Any = None,
        upsert: bool = False,
        return_document: Any = ReturnDocument.BEFORE,
        array_filters: Any = None,
    ) -> dict | None:
        """Update one document and return it as it was before or after."""
        return self._find_and_modify(
            filter, update, projection, sort, upsert, return_document, array_filters, None
        )

    def find_one_and_replace(
        self,
        filter: Any,
        replacement: Any,
        projection: Any = None,
        sort: Any = None,
        upsert: bool = False,
        return_document: Any = ReturnDocument.BEFORE,
    ) -> dict | None:
        return self._find_and_modify(
            filter, replacement, projection, sort, upsert, return_document, None, True
        )

    def find_one_and_delete(
        self,
        filter: Any,
        projection: Any = None,
        sort: Any = None,
    ) -> dict | None:
        """Delete one document and return it."""
        matches = self._read(filter)
        if not matches:
            return None
        document = matches[0]
        with self._storage.transaction():
            self._storage.delete(self._name, doc_key(document["_id"]))
        return _apply_projection(document, projection)

    def _find_and_modify(
        self,
        filter: Any,
        update: Any,
        projection: Any,
        sort: Any,
        upsert: bool,
        return_document: Any,
        array_filters: Any,
        replace: bool | None,
    ) -> dict | None:
        matches = self._read(filter)
        if not matches:
            if upsert:
                self._update(filter, update, True, array_filters, many=False, replace=bool(replace))
                return None
            return None

        original = matches[0]
        updated = dict(original)
        if replace:
            updated = dict(update)
            updated["_id"] = original["_id"]
        else:
            apply_update(updated, update, is_insert=False, array_filters=array_filters)
        updated = self._prepare(updated, is_insert=False)

        with self._storage.transaction():
            self._storage.replace(self._name, doc_key(updated["_id"]), updated)

        chosen = updated if return_document == ReturnDocument.AFTER else original
        return _apply_projection(chosen, projection)

    # -- indexes --------------------------------------------------------

    def create_index(self, keys: Any, unique: bool = False, name: str | None = None) -> str:
        """Create an index on one or more fields.

        ``keys`` may be a field name (``"email"``) or a list of
        ``(field, direction)`` pairs, which creates a *compound* index over
        those fields.
        """
        if isinstance(keys, str):
            fields = [(keys, 1)]
        elif isinstance(keys, (list, tuple)):
            fields = []
            for key in keys:
                if isinstance(key, (list, tuple)):
                    field, direction = key[0], key[1]
                else:
                    field, direction = key, 1
                if direction not in (1, -1):
                    raise WriteError(f"the direction for {field!r} must be 1 or -1")
                if direction == -1:
                    raise WriteError(
                        "descending indexes are not supported yet; "
                        "compound indexes are ascending"
                    )
                fields.append((field, direction))
        else:
            raise WriteError("create_index needs a field name or a list of fields")

        if not fields:
            raise WriteError("an index needs at least one field")

        paths = [field for field, _ in fields]
        index_name = name or "_".join(f"{field}_{direction}" for field, direction in fields)
        self._storage.create_index(self._name, index_name, paths, unique)
        return index_name

    def drop_index(self, name: str) -> None:
        """Remove an index."""
        self._storage.drop_index(self._name, name)

    def list_indexes(self) -> list[dict]:
        """The indexes on this collection."""
        indexes = [{"name": "_id_", "key": {"_id": 1}, "unique": True}]
        for name, paths, unique in self._storage.list_indexes(self._name):
            indexes.append(
                {"name": name, "key": {path: 1 for path in paths}, "unique": unique}
            )
        return indexes

    # -- collection level ----------------------------------------------

    def drop(self) -> None:
        """Delete the collection and everything in it."""
        self._database.drop_collection(self._name)

    def rename(self, new_name: str) -> None:
        """Rename the collection."""
        self._database.rename_collection(self._name, new_name)


def _set_nested(document: dict, path: str, value: Any) -> None:
    from .paths import set_path

    set_path(document, path, value)


def _apply_projection(document: dict, projection: Any) -> dict:
    """Apply a projection to a single document."""
    if not projection:
        return document
    from .aggregation import _stage_project

    return _stage_project([document], projection)[0]
