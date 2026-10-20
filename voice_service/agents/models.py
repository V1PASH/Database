"""The two collections this service keeps: ``models`` and ``agent``.

``models`` is a registry of the models a voice agent can be built from --
an LLM, a text-to-speech model or a speech-to-text model. ``agent`` holds
the agents themselves: a name, a prompt, and the three models it is wired
together from.

Both are stored in minidb. The Django classes below describe the shape of a
document and give the service a queryset to work with; the collections are
named ``models`` and ``agent`` exactly as the API exposes them.
"""

from __future__ import annotations

from django.db import models

from minidb.django import MinidbModel

__all__ = ["Model", "Agent", "MODEL_KINDS", "MODEL_SCHEMA", "AGENT_SCHEMA"]

#: The kinds of model the registry holds.
MODEL_KINDS = ("llm", "tts", "stt")

#: The schema the ``models`` collection is validated against.
MODEL_SCHEMA: dict = {
    "title": "model",
    "type": "object",
    "required": ["name", "slug", "kind"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]*$"},
        "handle": {"type": "string", "maxLength": 100},
        "kind": {"type": "string", "enum": list(MODEL_KINDS)},
        "description": {"type": "string"},
        "config": {"type": "object"},
        "active": {"type": "boolean", "default": True},
        "created_at": {"type": "date"},
    },
}

#: The schema the ``agent`` collection is validated against.
AGENT_SCHEMA: dict = {
    "title": "agent",
    "type": "object",
    "required": ["name", "slug"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]*$"},
        "handle": {"type": "string", "maxLength": 100},
        "prompt": {"type": "string"},
        "stt": {"type": "string", "maxLength": 200},
        "model": {"type": "string", "maxLength": 200},
        "tts": {"type": "string", "maxLength": 200},
        "config": {"type": "object"},
        "created_at": {"type": "date"},
    },
}


class Model(MinidbModel):
    """One entry in the model registry."""

    name = models.CharField(max_length=200)
    slug = models.CharField(max_length=200)
    handle = models.CharField(max_length=100, blank=True)
    kind = models.CharField(max_length=10)
    description = models.TextField(blank=True)
    config = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "agents"
        db_table = "models"

    def __str__(self) -> str:
        return f"{self.kind}:{self.slug}"


class Agent(MinidbModel):
    """A voice agent: a prompt plus the models it talks through."""

    name = models.CharField(max_length=200)
    slug = models.CharField(max_length=200)
    handle = models.CharField(max_length=100, blank=True)
    prompt = models.TextField(blank=True)
    stt = models.CharField(max_length=200, blank=True)
    model = models.CharField(max_length=200, blank=True)
    tts = models.CharField(max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "agents"
        db_table = "agent"

    def __str__(self) -> str:
        return self.slug
