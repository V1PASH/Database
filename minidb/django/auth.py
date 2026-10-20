"""A Django authentication backend backed by minidb.

    AUTHENTICATION_BACKENDS = ["minidb.django.auth.MinidbUserBackend"]

The users live in a minidb collection, one document per user. The password
is stored as a Django password hash, so ``check_password`` works as usual.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User

from .database import get_database

__all__ = ["MinidbUserBackend"]


class MinidbUserBackend(BaseBackend):
    """Authenticate against a minidb collection of users."""

    collection_name = "auth_user"
    username_field = "username"

    # -- helpers ---------------------------------------------------------

    @property
    def _collection(self):
        database = get_database()
        if self.collection_name not in database.list_collection_names():
            database.create_collection(self.collection_name)
        return database[self.collection_name]

    def _to_user(self, document: dict) -> User:
        """Build a Django user from a stored document."""
        user = User(
            username=document.get("username", ""),
            email=document.get("email", ""),
            first_name=document.get("first_name", ""),
            last_name=document.get("last_name", ""),
            is_staff=document.get("is_staff", False),
            is_active=document.get("is_active", True),
            is_superuser=document.get("is_superuser", False),
            last_login=document.get("last_login"),
        )
        user.id = document.get("_id")
        user.set_password(document.get("password", ""))
        return user

    # -- the backend interface -------------------------------------------

    def authenticate(self, request=None, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(self.username_field)
        if username is None or password is None:
            return None

        document = self._collection.find_one({self.username_field: username})
        if document is None:
            return None
        if not document.get("is_active", True):
            return None
        if not check_password(password, document.get("password", "")):
            return None

        user = self._to_user(document)
        user.last_login = _now()
        self._collection.replace_one({"_id": document["_id"]}, {**document, "last_login": user.last_login})
        return user

    def get_user(self, user_id):
        document = self._collection.find_one({"_id": user_id})
        if document is None:
            return None
        return self._to_user(document)

    def user_can_authenticate(self, user) -> bool:
        return bool(user and user.is_active)

    # -- creating users --------------------------------------------------

    def create_user(self, username: str, password: str, **extra: Any) -> dict:
        """Store a new user, with the password hashed."""
        document = {
            "username": username,
            "password": make_password(password),
            "email": extra.get("email", ""),
            "first_name": extra.get("first_name", ""),
            "last_name": extra.get("last_name", ""),
            "is_staff": extra.get("is_staff", False),
            "is_superuser": extra.get("is_superuser", False),
            "is_active": extra.get("is_active", True),
            "last_login": None,
        }
        result = self._collection.insert_one(document)
        return {**document, "_id": result.inserted_id}


def _now():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)
