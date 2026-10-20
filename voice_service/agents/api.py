"""The JSON API.

    GET    /api/health
    GET    /api/models?kind=llm&q=search
    POST   /api/models
    GET    /api/models/<slug>
    PATCH  /api/models/<slug>
    DELETE /api/models/<slug>
    GET    /api/agents?q=search
    POST   /api/agents
    GET    /api/agents/<slug>
    PATCH  /api/agents/<slug>
    DELETE /api/agents/<slug>
"""

from __future__ import annotations

import json
from typing import Any, Callable

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from minidb.bson import to_jsonable
from minidb.errors import ValidationError

from . import storage
from .storage import Conflict, Invalid, NotFound

__all__ = [
    "health",
    "model_collection",
    "model_detail",
    "agent_collection",
    "agent_detail",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _body(request: HttpRequest) -> dict:
    """The JSON body of a request."""
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Invalid(f"the request body is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise Invalid("the request body must be a JSON object")
    return payload


def _ok(payload: Any, status: int = 200) -> JsonResponse:
    """A JSON response, with the extended types written MongoDB's way."""
    return JsonResponse(
        to_jsonable(payload), status=status, json_dumps_params={"indent": 2}
    )


def _error(message: str, status: int = 400, details: list[str] | None = None) -> JsonResponse:
    body: dict[str, Any] = {"error": message}
    if details:
        body["details"] = details
    return JsonResponse(body, status=status, json_dumps_params={"indent": 2})


def _guard(function: Callable) -> Callable:
    """Turn the storage errors into HTTP responses."""

    def wrapper(request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            return function(request, *args, **kwargs)
        except NotFound as error:
            return _error(str(error), 404)
        except Conflict as error:
            return _error(str(error), 409)
        except Invalid as error:
            return _error(str(error), 400, error.details)
        except ValidationError as error:
            # The collection layer validates too; this is the safety net.
            return _error(str(error), 400, error.details)

    wrapper.__name__ = function.__name__
    wrapper.__doc__ = function.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@_guard
def health(request: HttpRequest) -> JsonResponse:
    """Is the service up, and what is in the database?"""
    from minidb import __version__

    return _ok(
        {
            "status": "ok",
            "minidb": __version__,
            "database": storage.get_database().path,
            **storage.stats(),
        }
    )


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


@_guard
def model_list(request: HttpRequest) -> JsonResponse:
    """List the models in the registry."""
    return model_collection(request)


@csrf_exempt
@_guard
def model_detail(request: HttpRequest, slug: str) -> JsonResponse:
    """One model, or a change to it."""
    if request.method == "GET":
        return _ok(storage.get_model(slug))

    if request.method == "POST" or request.method == "PUT":
        document = storage.create_model({**_body(request), "slug": slug})
        return _ok(document, 201)

    if request.method == "PATCH":
        return _ok(storage.update_model(slug, _body(request)))

    if request.method == "DELETE":
        storage.delete_model(slug)
        return _ok({"deleted": slug})

    return _error(f"{request.method} is not allowed here", 405)


@csrf_exempt
@_guard
def model_collection(request: HttpRequest) -> JsonResponse:
    """List the models, or create one."""
    if request.method == "GET":
        kind = request.GET.get("kind") or None
        if kind and kind not in ("llm", "tts", "stt"):
            return _error("kind must be one of llm, tts, stt", 400)
        models = storage.list_models(
            kind=kind,
            search=request.GET.get("q") or None,
            active_only=request.GET.get("active") == "true",
        )
        return _ok({"count": len(models), "results": models})

    if request.method == "POST":
        document = storage.create_model(_body(request))
        return _ok(document, 201)

    return _error(f"{request.method} is not allowed here", 405)


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


@csrf_exempt
@_guard
def agent_detail(request: HttpRequest, slug: str) -> JsonResponse:
    """One agent, or a change to it."""
    if request.method == "GET":
        return _ok(storage.get_agent(slug))

    if request.method == "POST" or request.method == "PUT":
        document = storage.create_agent({**_body(request), "slug": slug})
        return _ok(document, 201)

    if request.method == "PATCH":
        return _ok(storage.update_agent(slug, _body(request)))

    if request.method == "DELETE":
        storage.delete_agent(slug)
        return _ok({"deleted": slug})

    return _error(f"{request.method} is not allowed here", 405)


@csrf_exempt
@_guard
def agent_collection(request: HttpRequest) -> JsonResponse:
    """List the agents, or create one."""
    if request.method == "GET":
        agents = storage.list_agents(search=request.GET.get("q") or None)
        return _ok({"count": len(agents), "results": agents})

    if request.method == "POST":
        document = storage.create_agent(_body(request))
        return _ok(document, 201)

    return _error(f"{request.method} is not allowed here", 405)
