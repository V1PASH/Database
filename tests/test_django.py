"""Tests for the Django integration.

Django is configured at the top of this module, before anything from
``minidb.django`` is imported, so these tests need no project around them.
"""

from __future__ import annotations

import django
import pytest
from django.conf import settings

settings.configure(
    DEBUG=True,
    SECRET_KEY="tests",
    USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
    DATABASES={
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    },
    MINIDB_PATH=":memory:",
    CACHES={
        "default": {
            "BACKEND": "minidb.django.cache.MinidbCache",
            "LOCATION": ":memory:",
        }
    },
)
django.setup()

from django.db import models  # noqa: E402
from django.db.models import Q  # noqa: E402

from minidb.django import MinidbModel  # noqa: E402
from minidb.django.auth import MinidbUserBackend  # noqa: E402
from minidb.django.cache import MinidbCache  # noqa: E402
from minidb.django.database import get_database  # noqa: E402
from minidb.django.lookups import build_filter  # noqa: E402
from minidb.django.queryset import NotSupportedError  # noqa: E402
from minidb.django.sessions import SessionStore  # noqa: E402


class Article(MinidbModel):
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    views = models.IntegerField(default=0)
    published = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)

    class Meta:
        app_label = "auth"
        db_table = "articles"


class Comment(MinidbModel):
    article_id = models.IntegerField()
    author = models.CharField(max_length=100)
    text = models.TextField()

    class Meta:
        app_label = "auth"
        db_table = "comments"


@pytest.fixture(autouse=True)
def clean():
    """Start each test with empty collections."""
    database = get_database()
    for name in ("articles", "comments"):
        if name in database.list_collection_names():
            database[name].delete_many({})
    yield


# ---------------------------------------------------------------------------
# reading and writing
# ---------------------------------------------------------------------------


def test_create_and_read_back():
    article = Article.objects.create(title="Hello", views=3, published=True)
    assert article.pk is not None

    loaded = Article.objects.get(pk=article.pk)
    assert loaded.title == "Hello"
    assert loaded.views == 3
    assert loaded.published is True


def test_the_primary_key_is_the_document_id():
    article = Article.objects.create(title="Hello")
    document = get_database()["articles"].find_one({"_id": article.pk})
    assert document is not None
    assert document["title"] == "Hello"


def test_primary_keys_are_not_reused():
    first = Article.objects.create(title="One")
    second = Article.objects.create(title="Two")
    assert second.pk > first.pk


def test_saving_again_updates_the_document():
    article = Article.objects.create(title="Hello")
    article.title = "Goodbye"
    article.save()
    assert Article.objects.count() == 1
    assert Article.objects.get(pk=article.pk).title == "Goodbye"


def test_deleting():
    article = Article.objects.create(title="Hello")
    Article.objects.filter(pk=article.pk).delete()
    assert Article.objects.count() == 0


def test_updating():
    Article.objects.create(title="Hello", views=1)
    Article.objects.filter(title="Hello").update(views=99)
    assert Article.objects.get(title="Hello").views == 99


def test_json_fields_round_trip():
    article = Article.objects.create(title="Hello", tags=["a", "b"])
    assert Article.objects.get(pk=article.pk).tags == ["a", "b"]


# ---------------------------------------------------------------------------
# filtering
# ---------------------------------------------------------------------------


@pytest.fixture()
def articles():
    return [
        Article.objects.create(title="Alpha", views=10, published=True, tags=["x"]),
        Article.objects.create(title="Beta", views=20, published=False, tags=["y"]),
        Article.objects.create(title="Gamma", views=30, published=True, tags=["x", "z"]),
    ]


def titles(queryset):
    return sorted(article.title for article in queryset)


def test_filter_exact(articles):
    assert titles(Article.objects.filter(title="Alpha")) == ["Alpha"]


def test_filter_comparison(articles):
    assert titles(Article.objects.filter(views__gt=15)) == ["Beta", "Gamma"]
    assert titles(Article.objects.filter(views__gte=20)) == ["Beta", "Gamma"]
    assert titles(Article.objects.filter(views__lt=20)) == ["Alpha"]
    assert titles(Article.objects.filter(views__lte=20)) == ["Alpha", "Beta"]


def test_filter_in(articles):
    assert titles(Article.objects.filter(title__in=["Alpha", "Gamma"])) == ["Alpha", "Gamma"]


def test_filter_contains(articles):
    assert titles(Article.objects.filter(title__contains="amm")) == ["Gamma"]


def test_filter_icontains(articles):
    assert titles(Article.objects.filter(title__icontains="ALPHA")) == ["Alpha"]


def test_filter_startswith(articles):
    assert titles(Article.objects.filter(title__startswith="B")) == ["Beta"]


def test_filter_endswith(articles):
    assert titles(Article.objects.filter(title__endswith="a")) == ["Alpha", "Beta", "Gamma"]


def test_filter_boolean(articles):
    assert titles(Article.objects.filter(published=True)) == ["Alpha", "Gamma"]


def test_filter_by_pk(articles):
    first = articles[0]
    assert titles(Article.objects.filter(pk=first.pk)) == ["Alpha"]


def test_exclude(articles):
    assert titles(Article.objects.exclude(published=True)) == ["Beta"]


def test_q_objects(articles):
    found = Article.objects.filter(Q(published=False) | Q(views__gt=25))
    assert titles(found) == ["Beta", "Gamma"]


def test_q_objects_with_and(articles):
    found = Article.objects.filter(Q(published=True) & Q(views__gt=20))
    assert titles(found) == ["Gamma"]


def test_chained_filters(articles):
    found = Article.objects.filter(published=True).filter(views__gt=20)
    assert titles(found) == ["Gamma"]


def test_order_by(articles):
    assert [a.title for a in Article.objects.order_by("-views")] == [
        "Gamma",
        "Beta",
        "Alpha",
    ]


def test_order_by_then_filter(articles):
    found = Article.objects.order_by("views").filter(published=True)
    assert [a.title for a in found] == ["Alpha", "Gamma"]


def test_count_exists_first(articles):
    assert Article.objects.count() == 3
    assert Article.objects.filter(published=True).count() == 2
    assert Article.objects.filter(title="Nope").exists() is False
    assert Article.objects.first().title == "Alpha"


def test_get_raises_when_missing(articles):
    with pytest.raises(Article.DoesNotExist):
        Article.objects.get(title="Nope")


def test_values_and_values_list(articles):
    values = list(Article.objects.order_by("title").values("title", "views"))
    assert values[0] == {"title": "Alpha", "views": 10}

    flat = list(Article.objects.order_by("title").values_list("title", flat=True))
    assert flat == ["Alpha", "Beta", "Gamma"]

    tuples = list(Article.objects.order_by("title").values_list("title", "views"))
    assert tuples[0] == ("Alpha", 10)


def test_in_bulk(articles):
    found = Article.objects.in_bulk([articles[0].pk])
    assert list(found.values())[0].title == "Alpha"


def test_a_query_on_a_related_collection(articles):
    Comment.objects.create(article_id=articles[0].pk, author="ada", text="Nice")
    Comment.objects.create(article_id=articles[1].pk, author="alan", text="Agreed")
    found = Comment.objects.filter(article_id=articles[0].pk)
    assert [c.author for c in found] == ["ada"]


def test_explain_reports_the_plan(articles):
    plan = Article.objects.filter(title="Alpha").explain()
    assert plan["documentsMatched"] == 1
    assert plan["totalDocuments"] == 3


def test_annotations_are_refused_clearly(articles):
    with pytest.raises(NotSupportedError):
        Article.objects.annotate(total=1)


# ---------------------------------------------------------------------------
# the lookup translation on its own
# ---------------------------------------------------------------------------


def test_build_filter_translates_lookups():
    assert build_filter((), {"views__gt": 5}) == {"views": {"$gt": 5}}
    assert build_filter((), {"name": "x"}) == {"name": "x"}
    assert build_filter((), {"name__icontains": "x"}) == {
        "name": {"$regex": "x", "$options": "i"}
    }
    assert build_filter((), {"name__in": ["a", "b"]}) == {"name": {"$in": ["a", "b"]}}


def test_build_filter_renames_the_primary_key():
    assert build_filter((), {"pk": 3}, {"pk": "_id"}) == {"_id": 3}


def test_build_filter_handles_q_objects():
    query = Q(a=1) | Q(b=2)
    assert build_filter((query,), {}) == {"$or": [{"a": 1}, {"b": 2}]}


# ---------------------------------------------------------------------------
# the cache backend
# ---------------------------------------------------------------------------


def test_the_cache_backend():
    cache = MinidbCache(":memory:", {"COLLECTION": "cache"})
    cache.set("key", "value")
    assert cache.get("key") == "value"
    assert cache.has_key("key")
    assert cache.get_many(["key"]) == {"key": "value"}

    cache.set("key", {"a": 1})
    assert cache.get("key") == {"a": 1}

    cache.delete("key")
    assert cache.get("key") is None
    assert not cache.has_key("key")


def test_the_cache_backend_counts():
    cache = MinidbCache(":memory:", {"COLLECTION": "cache2"})
    cache.set("count", 1)
    assert cache.incr("count") == 2
    assert cache.decr("count") == 1


# ---------------------------------------------------------------------------
# the session engine
# ---------------------------------------------------------------------------


def test_the_session_engine():
    store = SessionStore()
    store["colour"] = "green"
    store.save()

    again = SessionStore(store.session_key)
    assert again["colour"] == "green"
    assert again.exists(session_key=store.session_key)

    again.delete()
    assert SessionStore(store.session_key).load() == {}


# ---------------------------------------------------------------------------
# the authentication backend
# ---------------------------------------------------------------------------


def test_the_authentication_backend():
    backend = MinidbUserBackend()
    backend.create_user("ada", "secret-password", email="ada@example.com")

    user = backend.authenticate(username="ada", password="secret-password")
    assert user is not None
    assert user.username == "ada"
    assert user.email == "ada@example.com"

    assert backend.authenticate(username="ada", password="wrong") is None
    assert backend.authenticate(username="nobody", password="secret-password") is None


def test_the_authentication_backend_fetches_users():
    backend = MinidbUserBackend()
    backend.create_user("ada", "secret-password")
    user = backend.get_user(backend._collection.find_one({"username": "ada"})["_id"])
    assert user is not None
    assert user.username == "ada"
