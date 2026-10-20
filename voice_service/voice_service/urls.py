"""URLs for the voice agent service."""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("", include("agents.urls")),
]
