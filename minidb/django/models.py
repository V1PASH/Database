"""The model base class.

    from django.db import models
    from minidb.django import MinidbModel

    class Article(MinidbModel):
        title = models.CharField(max_length=200)
        views = models.IntegerField(default=0)

        class Meta:
            app_label = "blog"

    Article.objects.create(title="Hello")
    Article.objects.filter(views__gt=10).order_by("-views")

Each model is one minidb collection, named after ``Meta.db_table``. Each row
is one document, and the Django primary key is stored as the document's
``_id``.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from .database import get_database
from .queryset import MinidbManager

__all__ = ["MinidbModel"]


class MinidbModel(models.Model):
    """A Django model stored as a minidb document."""

    objects = MinidbManager()

    class Meta:
        abstract = True

    # -- helpers ---------------------------------------------------------

    @classmethod
    def collection(cls):
        """The minidb collection this model lives in."""
        return get_database()[cls._meta.db_table]

    @classmethod
    def from_document(cls, document: dict) -> "MinidbModel":
        """Build a model instance from a stored document."""
        instance = cls()
        for field in cls._meta.concrete_fields:
            if field.primary_key:
                setattr(instance, field.attname, document.get("_id"))
            elif field.attname in document:
                setattr(instance, field.attname, document[field.attname])
        return instance

    def to_document(self) -> dict:
        """The document that represents this instance.

        ``pre_save`` is used rather than ``value_from_object`` because it
        is what applies ``auto_now`` and ``auto_now_add``.
        """
        document: dict[str, Any] = {}
        for field in self._meta.concrete_fields:
            if field.primary_key:
                continue
            document[field.attname] = field.pre_save(self, False)
        return document

    # -- writing ---------------------------------------------------------

    def save(self, *args, **kwargs) -> "MinidbModel":
        """Store the instance."""
        collection = self.collection()
        document = self.to_document()

        if self.pk is None:
            self.pk = collection._storage.next_id(self._meta.db_table)
        document["_id"] = self.pk

        existing = collection.find_one({"_id": self.pk})
        if existing is None:
            collection.insert_one(document)
        else:
            collection.replace_one({"_id": self.pk}, document)
        return self

    def delete(self, *args, **kwargs) -> tuple[int, dict]:
        """Remove the instance."""
        collection = self.collection()
        result = collection.delete_one({"_id": self.pk})
        return result.deleted_count, {}

    def refresh_from_db(self, using=None, fields=None, from_queryset=None) -> None:
        """Re-read the instance from the database."""
        if from_queryset is not None:
            documents = list(from_queryset._documents())
        else:
            document = self.collection().find_one({"_id": self.pk})
            documents = [document] if document else []
        if not documents:
            raise self.DoesNotExist(
                f"{self._meta.object_name} with id {self.pk!r} does not exist."
            )
        fresh = self.from_document(documents[0])
        for field in self._meta.concrete_fields:
            setattr(self, field.attname, getattr(fresh, field.attname))
