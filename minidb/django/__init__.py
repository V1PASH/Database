"""Django integration for minidb.

    # settings.py
    MINIDB_PATH = "app.db"
    AUTHENTICATION_BACKENDS = ["minidb.django.auth.MinidbUserBackend"]
    SESSION_ENGINE = "minidb.django.sessions.MinidbSessionEngine"
    CACHES = {"default": {"BACKEND": "minidb.django.cache.MinidbCache"}}

Models that live in minidb inherit from :class:`MinidbModel`::

    from django.db import models
    from minidb.django import MinidbModel

    class Article(MinidbModel):
        title = models.CharField(max_length=200)

        class Meta:
            app_label = "blog"

The queryset API works as usual -- ``filter``, ``exclude``, ``order_by``,
``get``, ``first``, ``count``, ``values``, ``values_list`` -- because the
lookups are translated into minidb's own query language rather than into
SQL.

What this is *not*: a Django database backend. Django's ORM speaks SQL and
minidb does not, so ``DATABASES`` still points at whatever Django needs for
its own tables (migrations, the content types table, the admin site). The
models that inherit from :class:`MinidbModel` are stored in minidb; the
models that inherit from ``models.Model`` are stored by Django as usual.
"""

from __future__ import annotations

from .database import get_database, reset_database, set_database
from .models import MinidbModel
from .queryset import MinidbManager, MinidbQuerySet

__all__ = [
    "MinidbModel",
    "MinidbQuerySet",
    "MinidbManager",
    "get_database",
    "set_database",
    "reset_database",
]
