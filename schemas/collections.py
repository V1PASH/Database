"""Schemas for the example application.

These are plain documents describing the shape of each collection. They are
attached to a collection with ``db.create_collection(name, validator=...)``
and are enforced on every write.

A good schema is the cheapest documentation you will ever write: it states
the contract, catches mistakes at the boundary, and keeps the data
trustworthy for everything downstream.
"""

from __future__ import annotations

from typing import Any

__all__ = ["USER_SCHEMA", "PROJECT_SCHEMA", "TASK_SCHEMA", "NOTE_SCHEMA", "SCHEMAS"]

USER_SCHEMA: dict[str, Any] = {
    "title": "user",
    "type": "object",
    "required": ["name", "email"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 120},
        "email": {"type": "string", "format": "email"},
        "role": {"type": "string", "enum": ["admin", "member", "viewer"], "default": "member"},
        "age": {"type": "integer", "minimum": 0, "maximum": 130},
        "active": {"type": "boolean", "default": True},
        "profile": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "timezone": {"type": "string"},
                "links": {
                    "type": "array",
                    "items": {"type": "string", "format": "uri"},
                },
            },
        },
    },
}

PROJECT_SCHEMA: dict[str, Any] = {
    "title": "project",
    "type": "object",
    "required": ["name", "slug"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "slug": {"type": "string", "pattern": "^[a-z0-9-]+$"},
        "description": {"type": "string"},
        "owner_id": {"type": "objectId"},
        "status": {
            "type": "string",
            "enum": ["active", "paused", "archived"],
            "default": "active",
        },
        "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "budget": {"type": "number", "minimum": 0},
        "meta": {"type": "object"},
    },
}

TASK_SCHEMA: dict[str, Any] = {
    "title": "task",
    "type": "object",
    "required": ["title", "project_id"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "project_id": {"type": "objectId"},
        "assignee_id": {"type": "objectId"},
        "status": {
            "type": "string",
            "enum": ["todo", "doing", "done", "cancelled"],
            "default": "todo",
        },
        "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "labels": {"type": "array", "items": {"type": "string"}},
        "estimate_hours": {"type": "number", "minimum": 0},
        "checklist": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "done": {"type": "boolean", "default": False},
                },
            },
        },
    },
}

NOTE_SCHEMA: dict[str, Any] = {
    "title": "note",
    "type": "object",
    "required": ["body"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "project_id": {"type": "objectId"},
        "pinned": {"type": "boolean", "default": False},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "users": USER_SCHEMA,
    "projects": PROJECT_SCHEMA,
    "tasks": TASK_SCHEMA,
    "notes": NOTE_SCHEMA,
}
