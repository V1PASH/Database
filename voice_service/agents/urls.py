"""URLs for the voice agent service."""

from __future__ import annotations

from django.urls import path

from . import api

urlpatterns = [
    path("api/health", api.health, name="health"),
    path("api/models", api.model_collection, name="model-list"),
    path("api/models/<slug:slug>", api.model_detail, name="model-detail"),
    path("api/agents", api.agent_collection, name="agent-list"),
    path("api/agents/<slug:slug>", api.agent_detail, name="agent-detail"),
]
