"""A QuerySet that runs against minidb instead of SQL.

Django's queryset API is reused -- ``filter``, ``exclude``, ``order_by``,
``get``, ``first``, ``count``, ``exists``, ``values`` and the rest all work
-- but the query itself is evaluated by minidb's matcher.
"""

from __future__ import annotations

from typing import Any, Iterator

from django.db import models
from django.db.models.query import BaseIterable

from .database import get_database
from .lookups import build_filter, merge_filters

__all__ = ["MinidbQuerySet", "MinidbManager", "NotSupportedError"]


class NotSupportedError(NotImplementedError):
    """Raised for the parts of the ORM that need a SQL database."""


def _selected_names(queryset) -> list[str]:
    """The field names a values() query asks for."""
    query = queryset.query
    if query.selected:
        return list(query.selected)
    return [*query.extra_select, *query.values_select, *query.annotation_select]


class MinidbModelIterable(BaseIterable):
    """Yields model instances built from the stored documents."""

    def __iter__(self):
        queryset = self.queryset
        for document in queryset._documents():
            yield queryset.model.from_document(document)


class MinidbValuesIterable(BaseIterable):
    """Yields dictionaries, the way ``values()`` does."""

    def __iter__(self):
        queryset = self.queryset
        names = _selected_names(queryset)
        pk_names = {queryset.model._meta.pk.attname, "pk"}
        for document in queryset._documents():
            row = {}
            for name in names:
                if name in pk_names:
                    row[name] = document.get("_id")
                else:
                    row[name] = document.get(name)
            yield row


class MinidbFlatValuesIterable(BaseIterable):
    """Yields one value per row, the way ``values_list(flat=True)`` does."""

    def __iter__(self):
        for row in MinidbValuesIterable(self.queryset):
            yield next(iter(row.values()))


class MinidbValuesListIterable(BaseIterable):
    """Yields tuples, the way ``values_list()`` does."""

    def __iter__(self):
        for row in MinidbValuesIterable(self.queryset):
            yield tuple(row.values())


class MinidbQuerySet(models.QuerySet):
    """A queryset backed by a minidb collection."""

    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query, using, hints)
        self._iterable_class = MinidbModelIterable
        self._minidb_filter: dict = {}
        self._minidb_sort: list[tuple[str, int]] = []

    # -- Django plumbing ------------------------------------------------

    def clone(self) -> "MinidbQuerySet":
        """Carry our own filter and sort across a clone."""
        clone = super().clone()
        clone._minidb_filter = dict(self._minidb_filter)
        clone._minidb_sort = list(self._minidb_sort)
        return clone

    def _chain(self, **kwargs) -> "MinidbQuerySet":
        clone = super()._chain(**kwargs)
        clone._minidb_filter = dict(self._minidb_filter)
        clone._minidb_sort = list(self._minidb_sort)
        return clone

    # -- building the query ---------------------------------------------

    def _aliases(self) -> dict[str, str]:
        """Django's ``pk`` and the model's own key live in the document's ``_id``."""
        pk_attname = self.model._meta.pk.attname
        return {"pk": "_id", pk_attname: "_id"}

    def filter(self, *args, **kwargs) -> "MinidbQuerySet":
        clone = self._chain()
        clone._minidb_filter = merge_filters(
            self._minidb_filter, build_filter(args, kwargs, self._aliases())
        )
        return clone

    def exclude(self, *args, **kwargs) -> "MinidbQuerySet":
        clone = self._chain()
        inner = build_filter(args, kwargs, self._aliases())
        if inner:
            clone._minidb_filter = merge_filters(
                self._minidb_filter, {"$nor": [inner]}
            )
        return clone

    def order_by(self, *fields: str) -> "MinidbQuerySet":
        clone = self._chain()
        sort: list[tuple[str, int]] = []
        for field in fields:
            if field in ("?",):
                continue
            direction = -1 if field.startswith("-") else 1
            name = field[1:] if field.startswith("-") else field
            sort.append((name.replace("__", "."), direction))
        clone._minidb_sort = sort
        return clone

    # -- running it ------------------------------------------------------

    def _collection(self):
        return get_database()[self.model._meta.db_table]

    def _documents(self) -> list[dict]:
        collection = self._collection()
        sort = self._minidb_sort or None
        return list(collection.find(self._minidb_filter, sort=sort))

    def _fetch_all(self) -> list:
        if self._result_cache is None:
            self._result_cache = list(self._iterable_class(self))
        return self._result_cache

    def values(self, *fields: str, **expressions) -> "MinidbQuerySet":
        """Dictionaries instead of model instances."""
        if expressions:
            raise NotSupportedError(
                "annotations need SQL, so they are not supported by minidb"
            )
        clone = super().values(*fields)
        clone._iterable_class = MinidbValuesIterable
        return clone

    def values_list(self, *fields: str, flat: bool = False, named: bool = False):
        """Tuples instead of model instances."""
        clone = super().values_list(*fields, flat=flat, named=named)
        clone._iterable_class = (
            MinidbFlatValuesIterable if flat else MinidbValuesListIterable
        )
        return clone

    def annotate(self, *args, **kwargs):
        raise NotSupportedError(
            "annotations need SQL, so they are not supported by minidb"
        )

    def select_related(self, *args, **kwargs):
        raise NotSupportedError(
            "select_related needs SQL, so it is not supported by minidb"
        )

    def prefetch_related(self, *args, **kwargs):
        raise NotSupportedError(
            "prefetch_related needs SQL, so it is not supported by minidb"
        )

    def iterator(self) -> Iterator:
        for document in self._documents():
            yield self.model.from_document(document)

    def count(self) -> int:
        return self._collection().count_documents(self._minidb_filter)

    def exists(self) -> bool:
        return self._collection().count_documents(self._minidb_filter) > 0

    def first(self) -> Any:
        for instance in self._fetch_all():
            return instance
        return None

    def last(self) -> Any:
        instances = self._fetch_all()
        return instances[-1] if instances else None

    def get(self, *args, **kwargs) -> Any:
        queryset = self.filter(*args, **kwargs) if (args or kwargs) else self
        matches = queryset._fetch_all()
        if not matches:
            raise self.model.DoesNotExist(
                f"{self.model._meta.object_name} matching query does not exist."
            )
        if len(matches) > 1:
            raise self.model.MultipleObjectsReturned(
                f"get() returned more than one {self.model._meta.object_name}."
            )
        return matches[0]

    def delete(self) -> tuple[int, dict]:
        collection = self._collection()
        result = collection.delete_many(self._minidb_filter)
        self._result_cache = None
        return result.deleted_count, {}

    def update(self, **kwargs) -> int:
        """Update every matching document."""
        collection = self._collection()
        expression = {"$set": {}}
        for field, value in kwargs.items():
            expression["$set"][field] = value
        if not expression["$set"]:
            return 0
        result = collection.update_many(self._minidb_filter, expression)
        self._result_cache = None
        return result.modified_count

    def explain(self) -> dict:
        """How would this query be answered?"""
        return self._collection().explain(self._minidb_filter)

    def in_bulk(self, id_list=None, field_name="pk") -> dict:
        """Documents by id, the way Django's ``in_bulk`` returns them."""
        collection = self._collection()
        identifiers = list(id_list or [])
        found = {}
        for identifier in identifiers:
            document = collection.find_one({"_id": identifier})
            if document:
                found[identifier] = self.model.from_document(document)
        return found


class MinidbManager(models.Manager.from_queryset(MinidbQuerySet)):
    """The manager that gives a model its queryset."""
