"""A web UI for browsing minidb databases.

    python -m minidb.webui            # every database file in this directory
    python -m minidb.webui app.db     # just this one
    python -m minidb.webui ~/databases # every database file in here

The server is deliberately small and dependency free: it is built on
``http.server`` from the standard library. It binds to localhost only and
has **no authentication**, so it is a local development tool -- do not
expose it to a network.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..bson import to_jsonable
from ..errors import MiniDBError
from ..objectid import ObjectId
from .workspace import Workspace

__all__ = ["serve", "create_server", "to_jsonable"]

STATIC_DIR = Path(__file__).parent / "static"


class _Handler(BaseHTTPRequestHandler):
    """Routes the HTTP requests onto the databases."""

    server_version = "minidb-webui"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------

    @property
    def workspace(self) -> Workspace:
        return self.server.workspace  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):  # type: ignore[attr-defined]
            super().log_message(format, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"the request body is not valid JSON: {exc}") from exc

    # -- routing -------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the name is fixed by the base class
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/") :])
            elif path == "/api/info":
                registry = self.workspace.registry
                self._send_json(
                    {
                        "workspace": str(registry) if registry else "none",
                        "roots": [str(root) for root in self.workspace.roots],
                        "version": _version(),
                    }
                )
            elif path == "/api/databases":
                self._send_json(self.workspace.databases())
            elif path.startswith("/api/databases/"):
                self._database_route(path, query)
            else:
                self._send_error_json("not found", 404)
        except MiniDBError as exc:
            self._send_error_json(str(exc))
        except ValueError as exc:
            self._send_error_json(str(exc))
        except FileNotFoundError as exc:
            self._send_error_json(str(exc), 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            body = self._read_json()

            if path == "/api/databases/remember":
                path_to_add = body.get("path")
                if not path_to_add:
                    raise ValueError("the request needs a 'path'")
                self.workspace.remember(path_to_add)
                self._send_json({"databases": self.workspace.databases()})
                return

            if not path.startswith("/api/databases/"):
                self._send_error_json("not found", 404)
                return

            database_name, collection_name, action = self._split(path)
            collection = self.workspace.database(database_name)[collection_name]

            if action == "query":
                self._send_json(self._run_query(collection, body))
            elif action == "aggregate":
                self._send_json(self._run_aggregate(collection, body))
            elif action == "explain":
                self._send_json(self._run_explain(collection, body))
            elif action == "insert":
                self._send_json(self._insert(collection, body))
            elif action == "update":
                self._send_json(self._update(collection, body))
            elif action == "delete":
                self._send_json(self._delete(collection, body))
            else:
                self._send_error_json(f"unknown action {action!r}", 404)
        except MiniDBError as exc:
            self._send_error_json(str(exc))
        except ValueError as exc:
            self._send_error_json(str(exc))
        except FileNotFoundError as exc:
            self._send_error_json(str(exc), 404)

    def do_DELETE(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)

        try:
            database_name, collection_name, action = self._split(path)
            if not action.startswith("documents/") or not collection_name:
                self._send_error_json("not found", 404)
                return

            document_id = action[len("documents/") :]
            collection = self.workspace.database(database_name)[collection_name]
            result = collection.delete_one({"_id": _parse_id(document_id)})
            self._send_json({"deleted": result.deleted_count})
        except MiniDBError as exc:
            self._send_error_json(str(exc))
        except ValueError as exc:
            self._send_error_json(str(exc))

    def _split(self, path: str) -> tuple[str, str, str]:
        """Pull (database, collection, action) out of a request path.

        The paths look like ``/api/databases/{db}/collections/{coll}/{action}``.
        """
        rest = path[len("/api/databases/") :]
        database_name, _, tail = rest.partition("/")
        if tail.startswith("collections/"):
            tail = tail[len("collections/") :]
        collection_name, _, action = tail.partition("/")
        return database_name, collection_name, action

    # -- static files --------------------------------------------------

    def _serve_static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send_error_json("not found", 404)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), content_type)

    # -- api actions ---------------------------------------------------

    def _database_route(self, path: str, query: dict[str, list[str]]) -> None:
        database_name, collection_name, action = self._split(path)

        if not collection_name:
            if not action:
                for entry in self.workspace.databases():
                    if entry["name"] == database_name:
                        self._send_json(entry)
                        return
                raise FileNotFoundError(f"no database called {database_name!r}")
            self._send_error_json("not found", 404)
            return

        collection = self.workspace.database(database_name)[collection_name]

        if not action:
            self._send_json(
                {
                    "name": collection_name,
                    "count": len(collection),
                    "indexes": collection.list_indexes(),
                    "validator": collection.validator.schema
                    if collection.validator
                    else None,
                }
            )
        elif action == "documents":
            filter = _parse_json_argument(query.get("filter", ["{}"])[0])
            sort = _parse_sort(query.get("sort", [""])[0])
            limit = int(query.get("limit", ["50"])[0] or 0)
            skip = int(query.get("skip", ["0"])[0] or 0)
            self._send_json(
                self._run_query(
                    collection,
                    {"filter": filter, "sort": sort, "limit": limit, "skip": skip},
                )
            )
        elif action == "indexes":
            self._send_json(collection.list_indexes())
        elif action == "stats":
            self._send_json(collection.stats())
        else:
            self._send_error_json(f"unknown action {action!r}", 404)

    def _run_query(self, collection: Any, body: dict) -> dict:
        filter = body.get("filter") or {}
        projection = body.get("projection") or None
        sort = body.get("sort") or None
        limit = int(body.get("limit") or 0)
        skip = int(body.get("skip") or 0)

        started = _now()
        documents = list(collection.find(filter, projection, sort, skip, limit))
        return {
            "documents": [to_jsonable(document) for document in documents],
            "count": len(documents),
            "total": len(collection),
            "milliseconds": _since(started),
        }

    def _run_explain(self, collection: Any, body: dict) -> dict:
        """Show how a query would be answered."""
        filter = body.get("filter") or {}
        return collection.explain(filter)

    def _run_aggregate(self, collection: Any, body: dict) -> dict:
        pipeline = body.get("pipeline") or []
        started = _now()
        documents = list(collection.aggregate(pipeline))
        return {
            "documents": [to_jsonable(document) for document in documents],
            "count": len(documents),
            "milliseconds": _since(started),
        }

    def _insert(self, collection: Any, body: dict) -> dict:
        document = body.get("document")
        if not isinstance(document, dict):
            raise ValueError("the request needs a 'document' object")
        result = collection.insert_one(document)
        return {"insertedId": to_jsonable(result.inserted_id)}

    def _update(self, collection: Any, body: dict) -> dict:
        filter = body.get("filter") or {}
        update = body.get("update") or {}
        many = bool(body.get("many"))
        if many:
            result = collection.update_many(filter, update)
        else:
            result = collection.update_one(filter, update)
        return {
            "matched": result.matched_count,
            "modified": result.modified_count,
            "upsertedId": to_jsonable(result.upserted_id)
            if result.upserted_id is not None
            else None,
        }

    def _delete(self, collection: Any, body: dict) -> dict:
        filter = body.get("filter") or {}
        many = bool(body.get("many"))
        if many:
            result = collection.delete_many(filter)
        else:
            result = collection.delete_one(filter)
        return {"deleted": result.deleted_count}


def _now() -> float:
    import time

    return time.perf_counter()


def _since(started: float) -> float:
    import time

    return round((time.perf_counter() - started) * 1000, 2)


def _version() -> str:
    from .. import __version__

    return __version__


def _parse_id(text: str) -> Any:
    """Turn an id from the URL back into the value it was stored as."""
    try:
        return ObjectId(text)
    except (ValueError, TypeError):
        return text


def _parse_json_argument(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"that is not valid JSON: {exc}") from exc


def _parse_sort(text: str) -> Any:
    """``"name"`` or ``"-age"`` becomes a sort specification."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("-"):
        return [(text[1:], -1)]
    if text.startswith("+"):
        return [(text[1:], 1)]
    return [(text, 1)]


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], workspace: Workspace, verbose: bool = False):
        super().__init__(address, _Handler)
        self.workspace = workspace
        self.verbose = verbose


def create_server(
    workspace: Workspace,
    host: str = "127.0.0.1",
    port: int = 5433,
    verbose: bool = False,
) -> _Server:
    """Build (but do not start) the web UI server."""
    return _Server((host, port), workspace, verbose)


def serve(
    workspace: Workspace,
    host: str = "127.0.0.1",
    port: int = 5433,
    verbose: bool = False,
    open_browser: bool = True,
) -> None:
    """Run the web UI until interrupted."""
    server = create_server(workspace, host, port, verbose)
    url = f"http://{host}:{port}/"

    databases = workspace.databases()
    print(f"minidb web UI  ->  {url}")
    print(f"workspace     ->  {', '.join(str(root) for root in workspace.roots)}")
    print(f"databases     ->  {len(databases)} found")
    for entry in databases:
        print(f"                 {entry['name']}  ({len(entry['collections'])} collections)")
    print("press ctrl+c to stop")

    if open_browser:
        threading.Timer(0.4, _open_browser, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
        server.server_close()
        workspace.close()


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - depends on the desktop session
        pass
