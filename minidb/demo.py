"""Sample data, so you have something to look at.

    python -c "from minidb import Database; from minidb.demo import seed; seed(Database('demo.db'))"
    minidb-ui demo.db
"""

from __future__ import annotations

from .database import Database

__all__ = ["seed"]

PEOPLE = [
    {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "role": "admin",
        "age": 36,
        "active": True,
        "profile": {"company": "Analytical Engines", "timezone": "Europe/London"},
    },
    {
        "name": "Alan Turing",
        "email": "alan@example.com",
        "role": "member",
        "age": 41,
        "active": True,
        "profile": {"company": "NPL", "timezone": "Europe/London"},
    },
    {
        "name": "Grace Hopper",
        "email": "grace@example.com",
        "role": "admin",
        "age": 85,
        "active": False,
        "profile": {"company": "US Navy", "timezone": "America/New_York"},
    },
    {
        "name": "Barbara Liskov",
        "email": "barbara@example.com",
        "role": "member",
        "age": 84,
        "active": True,
    },
    {
        "name": "Edsger Dijkstra",
        "email": "edsger@example.com",
        "role": "viewer",
        "age": 72,
        "active": True,
    },
]

PROJECTS = [
    {
        "name": "Analytical Engine",
        "slug": "analytical-engine",
        "status": "active",
        "budget": 50_000,
        "tags": ["history", "computing"],
        "description": "The general purpose mechanical computer.",
    },
    {
        "name": "COBOL",
        "slug": "cobol",
        "status": "archived",
        "budget": 12_000,
        "tags": ["compilers", "languages"],
        "description": "A business oriented language.",
    },
    {
        "name": "Grace Debugger",
        "slug": "grace-debugger",
        "status": "active",
        "budget": 31_500,
        "tags": ["debugging", "compilers"],
    },
    {
        "name": "The Algorithm",
        "slug": "the-algorithm",
        "status": "paused",
        "budget": 0,
        "tags": ["algorithms"],
    },
]

TASKS = [
    ("Write the notes", "todo", 2, 8.0),
    ("Build the mill", "doing", 1, 20.0),
    ("Order the parts", "todo", 3, 3.0),
    ("Prove the algorithm terminates", "done", 1, 12.0),
    ("Design the compiler", "doing", 2, 40.0),
    ("Find the bug", "todo", 1, 16.0),
]

NOTES = [
    ("Remember the punch cards", True, ["history"]),
    ("Mill #3 needs a new cam", False, ["maintenance"]),
    ("The compiler is nearly done", False, ["compilers"]),
]


def seed(database: Database) -> Database:
    """Fill a database with a small, realistic set of documents."""
    database.create_collection(
        "users",
        validator={
            "required": ["name", "email"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "email": {"type": "string", "format": "email"},
                "role": {
                    "type": "string",
                    "enum": ["admin", "member", "viewer"],
                    "default": "member",
                },
                "age": {"type": "integer", "minimum": 0},
                "active": {"type": "boolean", "default": True},
                "profile": {"type": "object"},
            },
        },
    )
    database.create_collection(
        "projects",
        validator={
            "required": ["name", "slug"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "slug": {"type": "string", "pattern": "^[a-z0-9-]+$"},
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "archived"],
                    "default": "active",
                },
                "budget": {"type": "number", "minimum": 0},
                "tags": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
            },
        },
    )
    database.create_collection(
        "tasks",
        validator={
            "required": ["title"],
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "project_id": {"type": "objectId"},
                "status": {
                    "type": "string",
                    "enum": ["todo", "doing", "done", "cancelled"],
                    "default": "todo",
                },
                "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                "estimate_hours": {"type": "number", "minimum": 0},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
        },
    )
    database.create_collection(
        "notes",
        validator={
            "required": ["body"],
            "additionalProperties": False,
            "properties": {
                "body": {"type": "string"},
                "project_id": {"type": "objectId"},
                "pinned": {"type": "boolean", "default": False},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    )

    for person in PEOPLE:
        database.users.insert_one(dict(person))
    for project in PROJECTS:
        database.projects.insert_one(dict(project))

    project_ids = [document["_id"] for document in database.projects.find()]
    for index, (title, status, priority, hours) in enumerate(TASKS):
        database.tasks.insert_one(
            {
                "title": title,
                "status": status,
                "priority": priority,
                "estimate_hours": hours,
                "project_id": project_ids[index % len(project_ids)],
                "labels": ["core"] if index % 2 == 0 else [],
            }
        )

    for body, pinned, tags in NOTES:
        database.notes.insert_one(
            {
                "body": body,
                "pinned": pinned,
                "tags": tags,
                "project_id": project_ids[0],
            }
        )

    database.users.create_index("email", unique=True)
    database.users.create_index("role")
    database.projects.create_index("slug", unique=True)
    database.projects.create_index([("status", 1), ("budget", 1)])
    database.tasks.create_index([("project_id", 1), ("status", 1)])
    database.tasks.create_index("priority")
    database.notes.create_index("pinned")

    return database
