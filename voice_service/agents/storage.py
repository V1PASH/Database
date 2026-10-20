"""The storage layer: everything the API does to the database.

A document store gives you no referential integrity, no unique constraints
and no cascading deletes. This module puts those back where the service
needs them, so the API above it can stay simple.
"""

from __future__ import annotations

from typing import Any

from minidb import Database
from minidb.errors import DuplicateKeyError, ValidationError

from .models import AGENT_SCHEMA, MODEL_KINDS, MODEL_SCHEMA

__all__ = [
    "get_database",
    "prepare",
    "list_models",
    "get_model",
    "create_model",
    "update_model",
    "delete_model",
    "list_agents",
    "get_agent",
    "create_agent",
    "update_agent",
    "delete_agent",
    "stats",
    "slugify",
    "NotFound",
    "Conflict",
    "Invalid",
]


class NotFound(Exception):
    """No such document."""


class Conflict(Exception):
    """The slug is already taken."""


class Invalid(Exception):
    """The document does not fit the schema, or a reference is broken."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []


def get_database() -> Database:
    """The minidb database, with the collections and indexes in place."""
    from minidb.django.database import get_database as django_get_database

    database = django_get_database()
    _ensure_collection(database, "models", MODEL_SCHEMA)
    _ensure_collection(database, "agent", AGENT_SCHEMA)
    return database


def _ensure_collection(database: Database, name: str, schema: dict) -> None:
    if name not in database.list_collection_names():
        database.create_collection(name, validator=schema)
    else:
        database.set_collection_validator(name, schema)

    collection = database[name]
    existing = {index["name"] for index in collection.list_indexes()}
    if "slug_1" not in existing:
        collection.create_index("slug", unique=True)
    if "kind_1" not in existing:
        collection.create_index("kind")


def slugify(text: str) -> str:
    """Turn a name into a slug."""
    result = []
    previous_dash = False
    for character in str(text).strip().lower():
        if character.isalnum() or character in "._":
            result.append(character)
            previous_dash = False
        elif not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-_")


def prepare(kind: str, payload: dict, existing: dict | None = None) -> dict:
    """Clean up an incoming document and check it against the schema.

    ``existing`` is the document being updated, if any. A PATCH merges into
    the stored document: the ``config`` dictionary in particular is merged
    key by key rather than replaced, so changing one setting does not throw
    the others away.
    """
    document = dict(existing or {})
    incoming = {key: value for key, value in payload.items() if value is not None}

    # Merge the config dictionary rather than replacing it, so changing one
    # setting does not throw the others away.
    if "config" in incoming and isinstance(incoming["config"], dict):
        merged = dict((existing or {}).get("config") or {})
        merged.update(incoming["config"])
        incoming["config"] = merged

    document.update(incoming)

    if "slug" not in document or not document.get("slug"):
        document["slug"] = slugify(document.get("name", ""))
    if "handle" not in document:
        document["handle"] = document["slug"]
    if kind == "model":
        document.setdefault("kind", "llm")
        if document["kind"] not in MODEL_KINDS:
            raise Invalid(
                f"kind must be one of {', '.join(MODEL_KINDS)}",
                [f"kind {document['kind']!r} is not a known kind"],
            )
        document.setdefault("active", True)
    document.setdefault("config", {})

    # The timestamp is set by the database layer, so a client cannot set it.
    if existing is None:
        document.pop("created_at", None)

    try:
        document = _validate(kind, document)
    except ValidationError as error:
        raise Invalid(str(error), error.details) from error

    return document


def _validate(kind: str, document: dict) -> dict:
    """Fill in the defaults and check the document against the schema."""
    from minidb.validation import Validator

    schema = MODEL_SCHEMA if kind == "model" else AGENT_SCHEMA
    validator = Validator(schema)
    document = validator.apply_defaults(dict(document))

    errors = validator.validate(document)
    if errors:
        raise Invalid(
            f"document failed validation: {'; '.join(errors)}", errors
        )
    return document


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def list_models(
    kind: str | None = None, search: str | None = None, active_only: bool = False
) -> list[dict]:
    """Every model in the registry."""
    collection = get_database()["models"]
    query: dict[str, Any] = {}
    if kind:
        query["kind"] = kind
    if active_only:
        query["active"] = True

    documents = list(collection.find(query))
    if search:
        needle = search.lower()
        documents = [
            document
            for document in documents
            if needle in str(document.get("name", "")).lower()
            or needle in str(document.get("slug", "")).lower()
            or needle in str(document.get("handle", "")).lower()
        ]
    return sorted(documents, key=lambda document: (document.get("kind", ""), document.get("slug", "")))


def get_model(slug: str) -> dict:
    document = get_database()["models"].find_one({"slug": slug})
    if document is None:
        raise NotFound(f"no model called {slug!r}")
    return document


def create_model(payload: dict) -> dict:
    document = prepare("model", payload)
    _check_model_references(document)
    collection = get_database()["models"]
    try:
        collection.insert_one(document)
    except DuplicateKeyError as error:
        raise Conflict(f"a model called {document['slug']!r} already exists") from error
    return get_model(document["slug"])


def update_model(slug: str, payload: dict) -> dict:
    collection = get_database()["models"]
    existing = collection.find_one({"slug": slug})
    if existing is None:
        raise NotFound(f"no model called {slug!r}")

    document = prepare("model", payload, existing)
    _check_model_references(document)
    try:
        collection.replace_one({"slug": slug}, document)
    except DuplicateKeyError as error:
        raise Conflict(f"a model called {document['slug']!r} already exists") from error
    return get_model(document["slug"])


def delete_model(slug: str) -> None:
    collection = get_database()["models"]
    if collection.find_one({"slug": slug}) is None:
        raise NotFound(f"no model called {slug!r}")

    # A model that an agent uses cannot just vanish.
    agents = get_database()["agent"]
    users = list(agents.find({"model": slug}))
    users += list(agents.find({"stt": slug}))
    users += list(agents.find({"tts": slug}))
    if users:
        names = sorted({agent.get("slug") for agent in users})
        raise Conflict(
            f"{slug!r} is used by {len(names)} agent(s): {', '.join(names)}"
        )
    collection.delete_one({"slug": slug})


def _check_model_references(document: dict) -> None:
    """A model may only be referenced by agents that exist."""
    for field in ("model", "stt", "tts"):
        slug = document.get(field)
        if not slug:
            continue
        if get_database()["models"].find_one({"slug": slug}) is None:
            raise Invalid(
                f"{field} {slug!r} does not name a known model",
                [f"{field}: {slug}"],
            )


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


def list_agents(search: str | None = None) -> list[dict]:
    collection = get_database()["agent"]
    documents = list(collection.find({}))
    if search:
        needle = search.lower()
        documents = [
            document
            for document in documents
            if needle in str(document.get("name", "")).lower()
            or needle in str(document.get("slug", "")).lower()
            or needle in str(document.get("handle", "")).lower()
        ]
    return sorted(documents, key=lambda document: document.get("slug", ""))


def get_agent(slug: str) -> dict:
    document = get_database()["agent"].find_one({"slug": slug})
    if document is None:
        raise NotFound(f"no agent called {slug!r}")
    return document


def create_agent(payload: dict) -> dict:
    document = prepare("agent", payload)
    _check_agent_references(document)
    collection = get_database()["agent"]
    try:
        collection.insert_one(document)
    except DuplicateKeyError as error:
        raise Conflict(f"an agent called {document['slug']!r} already exists") from error
    return get_agent(document["slug"])


def update_agent(slug: str, payload: dict) -> dict:
    collection = get_database()["agent"]
    existing = collection.find_one({"slug": slug})
    if existing is None:
        raise NotFound(f"no agent called {slug!r}")

    document = prepare("agent", payload, existing)
    _check_agent_references(document)
    try:
        collection.replace_one({"slug": slug}, document)
    except DuplicateKeyError as error:
        raise Conflict(f"an agent called {document['slug']!r} already exists") from error
    return get_agent(document["slug"])


def delete_agent(slug: str) -> None:
    collection = get_database()["agent"]
    if collection.find_one({"slug": slug}) is None:
        raise NotFound(f"no agent called {slug!r}")
    collection.delete_one({"slug": slug})


def _check_agent_references(document: dict) -> None:
    """The models an agent names have to exist, and be the right kind."""
    wanted = {"stt": "stt", "model": "llm", "tts": "tts"}
    for field, kind in wanted.items():
        slug = document.get(field)
        if not slug:
            continue
        model = get_database()["models"].find_one({"slug": slug})
        if model is None:
            raise Invalid(
                f"{field} {slug!r} does not name a known model",
                [f"{field}: {slug}"],
            )
        if model.get("kind") != kind:
            raise Invalid(
                f"{field} must name a {kind} model, but {slug!r} is a "
                f"{model.get('kind')} model",
                [f"{field}: {slug}"],
            )


# ---------------------------------------------------------------------------
# a summary
# ---------------------------------------------------------------------------


def stats() -> dict:
    """How much is in the database."""
    database = get_database()
    models = database["models"]
    agents = database["agent"]

    by_kind: dict[str, int] = {kind: 0 for kind in MODEL_KINDS}
    for document in models.find({}):
        kind = document.get("kind", "llm")
        by_kind[kind] = by_kind.get(kind, 0) + 1

    return {
        "models": models.count_documents({}),
        "modelsByKind": by_kind,
        "agents": agents.count_documents({}),
    }
