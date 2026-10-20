"""A Django session engine backed by minidb.

    SESSION_ENGINE = "minidb.django.sessions"

Django imports the module and looks for a class called ``SessionStore`` in
it, which is what :class:`SessionStore` is.
"""

from __future__ import annotations


from django.contrib.sessions.backends.base import CreateError, SessionBase

from .database import get_database

__all__ = ["SessionStore", "MinidbSessionEngine"]


class SessionStore(SessionBase):
    """Stores sessions as documents in a minidb collection."""

    collection_name = "sessions"

    @property
    def _collection(self):
        database = get_database()
        if self.collection_name not in database.list_collection_names():
            database.create_collection(self.collection_name)
        return database[self.collection_name]

    # -- reading ---------------------------------------------------------

    def load(self) -> dict:
        """The session data, or an empty session if it is not there."""
        document = self._collection.find_one({"_id": self.session_key})
        if document is None:
            return {}
        if document.get("expire_date") and document["expire_date"] <= _now():
            return {}
        return document.get("data", {})

    def exists(self, session_key: str) -> bool:
        document = self._collection.find_one({"_id": session_key})
        if document is None:
            return False
        if document.get("expire_date") and document["expire_date"] <= _now():
            return False
        return True

    # -- writing ---------------------------------------------------------

    def create(self) -> None:
        while True:
            self._session_key = self._get_new_session_key()
            try:
                self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return

    def save(self, must_create: bool = False) -> None:
        # Django generates the key lazily; a session that has never been
        # saved needs one before it can be written.
        if self.session_key is None:
            self.create()
            return

        document = {
            "_id": self.session_key,
            "data": self._get_session(no_load=must_create),
            "expire_date": self.get_expiry_date(),
        }
        if must_create:
            if self._collection.find_one({"_id": self.session_key}) is not None:
                raise CreateError
            self._collection.insert_one(document)
        else:
            self._collection.replace_one({"_id": self.session_key}, document)

    def delete(self, session_key: str | None = None) -> None:
        self._collection.delete_one({"_id": session_key or self.session_key})

    def cycle_key(self) -> None:
        """Give the session a new key, keeping the data."""
        data = self._session
        self.create()
        self._session_cache = data

    @classmethod
    def clear_expired(cls) -> None:
        """Remove the sessions that have run out."""
        database = get_database()
        if cls.collection_name not in database.list_collection_names():
            return
        database[cls.collection_name].delete_many({"expire_date": {"$lte": _now()}})


#: The old name, kept so existing settings keep working.
MinidbSessionEngine = SessionStore


def _now():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)
