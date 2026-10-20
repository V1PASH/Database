"""Tests for the web UI's HTTP API."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from minidb import Database
from minidb.webui.server import create_server, to_jsonable
from minidb.webui.workspace import Workspace


@pytest.fixture()
def workspace(tmp_path):
    """A workspace with two databases in it."""
    first = tmp_path / "shop.db"
    second = tmp_path / "notes.db"

    database = Database(str(first))
    database.create_collection("orders")
    database.orders.insert_many(
        [{"item": "book", "price": 12}, {"item": "pen", "price": 2}]
    )
    database.close()

    other = Database(str(second))
    other.create_collection("ideas")
    other.ideas.insert_one({"text": "write a database"})
    other.close()

    space = Workspace([tmp_path], use_registry=False)
    yield space
    space.close()


@pytest.fixture()
def server(workspace):
    httpd = create_server(workspace, host="127.0.0.1", port=0)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()

    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"

    httpd.shutdown()
    httpd.server_close()


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def post(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def delete(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def documents_url(database: str, collection: str, filter: str | None = None) -> str:
    """Build a documents URL with the filter properly encoded."""
    url = f"/api/databases/{database}/collections/{collection}/documents"
    if filter is not None:
        url += "?filter=" + urllib.parse.quote(filter)
    return url


# ---------------------------------------------------------------------------


def test_the_ui_page_is_served(server):
    status, body = get(f"{server}/")
    assert status == 200
    assert "<title>minidb</title>" in body


def test_the_static_files_are_served(server):
    assert get(f"{server}/static/app.css")[0] == 200
    assert get(f"{server}/static/app.js")[0] == 200


def test_info(server):
    status, body = get(f"{server}/api/info")
    assert status == 200
    assert "version" in body


def test_every_database_is_listed(server):
    status, body = get(f"{server}/api/databases")
    assert status == 200
    payload = json.loads(body)
    names = sorted(entry["name"] for entry in payload)
    assert names == ["notes", "shop"]
    for entry in payload:
        assert "path" in entry


def test_a_database_lists_its_collections(server):
    status, body = get(f"{server}/api/databases/shop")
    assert status == 200
    payload = json.loads(body)
    assert payload["name"] == "shop"
    assert payload["collections"][0]["name"] == "orders"
    assert payload["collections"][0]["count"] == 2


def test_fetching_documents(server):
    status, body = get(f"{server}" + documents_url("shop", "orders"))
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == 2
    assert payload["documents"][0]["item"] == "book"


def test_querying_with_a_filter(server):
    status, body = get(
        f"{server}" + documents_url("shop", "orders", '{"price": {"$gt": 5}}')
    )
    assert status == 200
    assert json.loads(body)["count"] == 1


def test_querying_through_the_post_endpoint(server):
    status, payload = post(
        f"{server}/api/databases/shop/collections/orders/query",
        {"filter": {"item": "pen"}, "projection": {"item": 1, "_id": 0}},
    )
    assert status == 200
    assert payload["documents"] == [{"item": "pen"}]


def test_sorting(server):
    status, payload = post(
        f"{server}/api/databases/shop/collections/orders/query",
        {"filter": {}, "sort": [["price", -1]]},
    )
    assert status == 200
    assert [d["price"] for d in payload["documents"]] == [12, 2]


def test_aggregating(server):
    status, payload = post(
        f"{server}/api/databases/shop/collections/orders/aggregate",
        {"pipeline": [{"$group": {"_id": None, "total": {"$sum": "$price"}}}]},
    )
    assert status == 200
    assert payload["documents"][0]["total"] == 14


def test_collection_stats(server):
    status, body = get(f"{server}/api/databases/shop/collections/orders/stats")
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == 2
    assert payload["dataSize"] > 0
    names = {field["name"] for field in payload["schema"]}
    assert "item" in names and "price" in names


def test_explain(server):
    status, payload = post(
        f"{server}/api/databases/shop/collections/orders/explain",
        {"filter": {"item": "book"}},
    )
    assert status == 200
    assert payload["strategy"] in ("IXSCAN", "COLLSCAN")
    assert payload["totalDocuments"] == 2
    assert payload["documentsMatched"] == 1


def test_inserting(server):
    status, payload = post(
        f"{server}/api/databases/shop/collections/orders/insert",
        {"document": {"item": "lamp", "price": 30}},
    )
    assert status == 200
    assert "$oid" in payload["insertedId"]

    status, body = get(f"{server}/api/databases/shop")
    assert json.loads(body)["collections"][0]["count"] == 3


def test_updating(server):
    status, payload = post(
        f"{server}/api/databases/shop/collections/orders/update",
        {"filter": {"item": "book"}, "update": {"$set": {"price": 15}}},
    )
    assert status == 200
    assert payload["modified"] == 1

    status, body = get(f"{server}" + documents_url("shop", "orders", '{"item": "book"}'))
    assert json.loads(body)["documents"][0]["price"] == 15


def test_deleting(server):
    status, body = get(f"{server}" + documents_url("shop", "orders", '{"item": "book"}'))
    document_id = json.loads(body)["documents"][0]["_id"]["$oid"]

    status, payload = delete(
        f"{server}/api/databases/shop/collections/orders/documents/{document_id}"
    )
    assert status == 200
    assert payload["deleted"] == 1

    status, body = get(f"{server}/api/databases/shop")
    assert json.loads(body)["collections"][0]["count"] == 1


def test_a_bad_filter_is_reported(server):
    status, body = get(f"{server}" + documents_url("shop", "orders", "{not json}"))
    assert status == 400
    assert "error" in json.loads(body)


def test_an_unknown_database_is_reported(server):
    status, body = get(f"{server}/api/databases/nothing")
    assert status == 404
    assert "error" in json.loads(body)


def test_an_unknown_route_is_a_404(server):
    assert get(f"{server}/nowhere")[0] == 404


def test_a_database_can_be_opened_at_runtime(server, tmp_path):
    extra = tmp_path / "extra.db"
    database = Database(str(extra))
    database.create_collection("stuff")
    database.stuff.insert_one({"a": 1})
    database.close()

    status, payload = post(
        f"{server}/api/databases/remember", {"path": str(extra)}
    )
    assert status == 200
    assert "extra" in [entry["name"] for entry in payload["databases"]]

    status, body = get(f"{server}/api/databases/extra")
    assert status == 200
    assert json.loads(body)["collections"][0]["name"] == "stuff"


def test_the_workspace_finds_databases_in_a_directory(tmp_path):
    for name in ("a.db", "b.db", "notes.txt"):
        (tmp_path / name).write_text("")

    space = Workspace([tmp_path], use_registry=False)
    try:
        found = sorted(path.name for path in space.discover())
        assert found == ["a.db", "b.db"]
    finally:
        space.close()


def test_the_workspace_names_databases_after_their_file(tmp_path):
    (tmp_path / "shop.db").write_text("")

    space = Workspace([tmp_path], use_registry=False)
    try:
        assert space.databases()[0]["name"] == "shop"
    finally:
        space.close()


def test_extended_types_are_shown_the_mongo_way():
    import datetime as _dt
    from decimal import Decimal

    from minidb import ObjectId

    identifier = ObjectId()
    assert to_jsonable(identifier) == {"$oid": str(identifier)}
    assert to_jsonable(Decimal("1.5")) == {"$numberDecimal": "1.5"}
    assert to_jsonable(b"hi") == {"$binary": "aGk="}
    assert "$date" in to_jsonable(_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc))
    assert to_jsonable({"a": [1, "b"]}) == {"a": [1, "b"]}


def test_the_frontend_javascript_runs():
    """The browser code is exercised in a small DOM shim."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = Path(__file__).parent / "frontend_smoke.mjs"
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
