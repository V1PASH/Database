"""A web UI for browsing a minidb database.

    python -m minidb.webui app.db
    minidb-ui app.db --port 8080

The server is deliberately small and dependency free: it is built on
``http.server`` from the standard library. It binds to localhost only and
has **no authentication**, so it is a local development tool -- do not
expose it to a network.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import threading
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..database import Database
from ..errors import MiniDBError
from ..objectid import ObjectId

__all__ = ["serve", "create_server", "to_jsonable"]

STATIC_DIR = Path(__file__).parent / "static"


def to_jsonable(value: Any) -> Any:
    """Convert a stored value into something ``json.dumps`` can handle.

    The extended types are written the way MongoDB's Extended JSON writes
    them, so the output is familiar to anyone who has used a MongoDB shell.
    """
    if isinstance(value, ObjectId):
        return {"$oid": str(value)}
    if isinstance(value, _dt.datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, _dt.date):
        return {"$date": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, Decimal):
        return {"$numberDecimal": str(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"$binary": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


class _Handler(BaseHTTPRequestHandler):
    """Routes the HTTP requests onto the database."""

    server_version = "minidb-webui"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------

    @property
    def database(self) -> Database:
        return self.server.database  # type: ignore[attr-defined]

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
            elif path == "/api/collections":
                self._send_json(self._list_collections())
            elif path.startswith("/api/collections/"):
                self._collection_route(path, query)
            elif path == "/api/info":
                self._send_json(
                    {
                        "database": self.database.name,
                        "path": self.database.path,
                        "version": _version(),
                    }
                )
            else:
                self._send_error_json("not found", 404)
        except MiniDBError as exc:
            self._send_error_json(str(exc))
        except ValueError as exc:
            self._send_error_json(str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            body = self._read_json()
            if not path.startswith("/api/collections/"):
                self._send_error_json("not found", 404)
                return

            rest = path[len("/api/collections/") :]
            name, _, tail = rest.partition("/")
            action, _, _ = tail.partition("/")
            collection = self.database[name]

            if action == "query":
                self._send_json(self._run_query(collection, body))
            elif action == "aggregate":
                self._send_json(self._run_aggregate(collection, body))
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

    def do_DELETE(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)

        try:
            if not path.startswith("/api/collections/"):
                self._send_error_json("not found", 404)
                return

            name, _, tail = path[len("/api/collections/") :].partition("/")
            action, _, document_id = tail.partition("/")
            if action != "documents" or not document_id:
                self._send_error_json("not found", 404)
                return

            collection = self.database[name]
            result = collection.delete_one({"_id": _parse_id(document_id)})
            self._send_json({"deleted": result.deleted_count})
        except MiniDBError as exc:
            self._send_error_json(str(exc))
        except ValueError as exc:
            self._send_error_json(str(exc))

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

    def _list_collections(self) -> list[dict]:
        collections = []
        for name in self.database.list_collection_names():
            collection = self.database[name]
            collections.append(
                {
                    "name": name,
                    "count": len(collection),
                    "indexes": collection.list_indexes(),
                }
            )
        return collections

    def _collection_route(self, path: str, query: dict[str, list[str]]) -> None:
        rest = path[len("/api/collections/") :]
        name, _, action = rest.partition("/")
        collection = self.database[name]

        if not action:
            self._send_json(
                {
                    "name": name,
                    "count": len(collection),
                    "indexes": collection.list_indexes(),
                    "validator": collection.validator.schema if collection.validator else None,
                }
            )
        elif action == "documents":
            filter = _parse_json_argument(query.get("filter", ["{}"])[0])
            sort = _parse_sort(query.get("sort", [""])[0])
            limit = int(query.get("limit", ["50"])[0] or 0)
            skip = int(query.get("skip", ["0"])[0] or 0)
            self._send_json(self._run_query(collection, {
                "filter": filter,
                "sort": sort,
                "limit": limit,
                "skip": skip,
            }))
        elif action == "indexes":
            self._send_json(collection.list_indexes())
        else:
            self._send_error_json(f"unknown action {action!r}", 404)

    def _run_query(self, collection: Any, body: dict) -> dict:
        filter = body.get("filter") or {}
        projection = body.get("projection") or None
        sort = body.get("sort") or None
        limit = int(body.get("limit") or 0)
        skip = int(body.get("skip") or 0)

        started = _now()
        documents = list(
            collection.find(filter, projection, sort, skip, limit)
        )
        return {
            "documents": [to_jsonable(document) for document in documents],
            "count": len(documents),
            "total": len(collection),
            "milliseconds": _since(started),
        }

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

    def __init__(self, address: tuple[str, int], database: Database, verbose: bool = False):
        super().__init__(address, _Handler)
        self.database = database
        self.verbose = verbose


def create_server(
    database: Database, host: str = "127.0.0.1", port: int = 8080, verbose: bool = False
) -> _Server:
    """Build (but do not start) the web UI server."""
    return _Server((host, port), database, verbose)


def serve(
    database: Database,
    host: str = "127.0.0.1",
    port: int = 8080,
    verbose: bool = False,
    open_browser: bool = True,
) -> None:
    """Run the web UI until interrupted."""
    server = create_server(database, host, port, verbose)
    url = f"http://{host}:{port}/"

    print(f"minidb web UI  ->  {url}")
    print(f"database       ->  {database.path}")
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


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - depends on the desktop session
        pass
