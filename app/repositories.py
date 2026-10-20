"""The application's access layer.

Everything the application knows about the database lives here. The rest of
the app never touches a collection directly, which means the storage engine
can be replaced (or the whole thing pointed at a real MongoDB server)
without touching application code.
"""

from __future__ import annotations

from typing import Any

from minidb import Database, ObjectId
from minidb.errors import DuplicateKeyError, ValidationError

__all__ = ["UserRepository", "ProjectRepository", "TaskRepository", "NoteRepository"]


class _Repository:
    collection_name = ""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._collection = db[self.collection_name]

    def _require(self, document: dict | None, message: str) -> dict:
        if document is None:
            raise LookupError(message)
        return document


class UserRepository(_Repository):
    collection_name = "users"

    def create(self, name: str, email: str, **extra: Any) -> dict:
        """Create a user. The schema fills in the defaults."""
        try:
            result = self._collection.insert_one({"name": name, "email": email, **extra})
        except DuplicateKeyError as exc:
            raise ValueError(f"a user with the email {email!r} already exists") from exc
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self._collection.find_one({"_id": result.inserted_id})

    def get(self, user_id: ObjectId | str) -> dict | None:
        """One user by id."""
        return self._collection.find_one({"_id": ObjectId(user_id) if isinstance(user_id, str) else user_id})

    def by_email(self, email: str) -> dict | None:
        """One user by email (this uses the unique index on ``email``)."""
        return self._collection.find_one({"email": email})

    def list(
        self, role: str | None = None, limit: int = 50, skip: int = 0
    ) -> list[dict]:
        """Users, newest last."""
        query = {"role": role} if role else {}
        return list(self._collection.find(query).sort([("name", 1)]).skip(skip).limit(limit))

    def count(self) -> int:
        return self._collection.count_documents()

    def deactivate(self, user_id: ObjectId) -> bool:
        """Mark a user as inactive."""
        result = self._collection.update_one({"_id": user_id}, {"$set": {"active": False}})
        return result.modified_count > 0

    def promote(self, user_id: ObjectId, role: str) -> bool:
        result = self._collection.update_one({"_id": user_id}, {"$set": {"role": role}})
        return result.modified_count > 0

    def roles(self) -> list[str]:
        """Every role in use."""
        return self._collection.distinct("role")


class ProjectRepository(_Repository):
    collection_name = "projects"

    def create(self, name: str, slug: str, owner_id: ObjectId | None = None, **extra: Any) -> dict:
        document: dict[str, Any] = {"name": name, "slug": slug}
        if owner_id is not None:
            document["owner_id"] = owner_id
        document.update(extra)
        try:
            result = self._collection.insert_one(document)
        except DuplicateKeyError as exc:
            raise ValueError(f"the slug {slug!r} is already taken") from exc
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self._collection.find_one({"_id": result.inserted_id})

    def get(self, project_id: ObjectId | str) -> dict | None:
        return self._collection.find_one(
            {"_id": ObjectId(project_id) if isinstance(project_id, str) else project_id}
        )

    def by_slug(self, slug: str) -> dict | None:
        return self._collection.find_one({"slug": slug})

    def list(self, status: str | None = None, tag: str | None = None) -> list[dict]:
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        if tag:
            query["tags"] = tag
        return list(self._collection.find(query).sort([("name", 1)]))

    def set_status(self, project_id: ObjectId, status: str) -> bool:
        result = self._collection.update_one({"_id": project_id}, {"$set": {"status": status}})
        return result.modified_count > 0

    def add_tag(self, project_id: ObjectId, tag: str) -> bool:
        result = self._collection.update_one(
            {"_id": project_id}, {"$addToSet": {"tags": tag}}
        )
        return result.modified_count > 0

    def budget_report(self) -> list[dict]:
        """Total budget per project, using the aggregation pipeline."""
        return list(
            self._collection.aggregate(
                [
                    {"$match": {"budget": {"$exists": True}}},
                    {
                        "$group": {
                            "_id": "$name",
                            "total_budget": {"$sum": "$budget"},
                            "projects": {"$sum": 1},
                        }
                    },
                    {"$sort": {"total_budget": -1}},
                ]
            )
        )


class TaskRepository(_Repository):
    collection_name = "tasks"

    def create(
        self, title: str, project_id: ObjectId, **extra: Any
    ) -> dict:
        try:
            result = self._collection.insert_one(
                {"title": title, "project_id": project_id, **extra}
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self._collection.find_one({"_id": result.inserted_id})

    def get(self, task_id: ObjectId | str) -> dict | None:
        return self._collection.find_one(
            {"_id": ObjectId(task_id) if isinstance(task_id, str) else task_id}
        )

    def list_for_project(
        self, project_id: ObjectId, status: str | None = None
    ) -> list[dict]:
        query: dict[str, Any] = {"project_id": project_id}
        if status:
            query["status"] = status
        return list(self._collection.find(query).sort([("priority", 1), ("title", 1)]))

    def list_for_assignee(self, assignee_id: ObjectId, status: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"assignee_id": assignee_id}
        if status:
            query["status"] = status
        return list(self._collection.find(query))

    def complete(self, task_id: ObjectId) -> bool:
        result = self._collection.update_one(
            {"_id": task_id}, {"$set": {"status": "done"}}
        )
        return result.modified_count > 0

    def add_label(self, task_id: ObjectId, label: str) -> bool:
        result = self._collection.update_one(
            {"_id": task_id}, {"$addToSet": {"labels": label}}
        )
        return result.modified_count > 0

    def add_checklist_item(self, task_id: ObjectId, text: str) -> bool:
        result = self._collection.update_one(
            {"_id": task_id}, {"$push": {"checklist": {"text": text, "done": False}}}
        )
        return result.modified_count > 0

    def toggle_checklist_item(self, task_id: ObjectId, position: int) -> bool:
        """Flip one checklist entry using an array filter."""
        result = self._collection.update_one(
            {"_id": task_id},
            {"$set": {"checklist.$[item].done": True}},
            array_filters=[{"item.text": {"$exists": True}}],
        )
        return result.modified_count > 0

    def estimate_total(self, project_id: ObjectId) -> float:
        """Sum of the estimates for a project."""
        rows = list(
            self._collection.aggregate(
                [
                    {"$match": {"project_id": project_id}},
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": "$estimate_hours"},
                            "count": {"$sum": 1},
                        }
                    },
                ]
            )
        )
        return rows[0]["total"] if rows else 0.0

    def status_breakdown(self) -> list[dict]:
        """How many tasks sit in each status."""
        return list(
            self._collection.aggregate(
                [
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ]
            )
        )


class NoteRepository(_Repository):
    collection_name = "notes"

    def create(self, body: str, **extra: Any) -> dict:
        try:
            result = self._collection.insert_one({"body": body, **extra})
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self._collection.find_one({"_id": result.inserted_id})

    def list_for_project(self, project_id: ObjectId) -> list[dict]:
        return list(self._collection.find({"project_id": project_id}))

    def pinned(self) -> list[dict]:
        return list(self._collection.find({"pinned": True}))

    def search(self, needle: str) -> list[dict]:
        """Notes whose title or body contains ``needle``."""
        import re

        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        return list(self._collection.find({"body": {"$regex": pattern}}))
