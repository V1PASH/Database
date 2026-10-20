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


@pytest.fixture()
def server():
    database = Database(":memory:")
    database.create_collection("people")
    database.people.insert_many(
        [
            {"name": "Ada", "age": 36, "tags": ["math"]},
            {"name": "Alan", "age": 41, "tags": []},
            {"name": "Grace", "age": 29},
        ]
    )

    httpd = create_server(database, host="127.0.0.1", port=0)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()

    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"

    httpd.shutdown()
    httpd.server_close()
    database.close()


def documents_url(collection: str, filter: str | None = None) -> str:
    """Build a documents URL with the filter properly encoded."""
    url = f"/api/collections/{collection}/documents"
    if filter is not None:
        url += "?filter=" + urllib.parse.quote(filter)
    return url


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


def test_listing_collections(server):
    status, body = get(f"{server}/api/collections")
    assert status == 200
    payload = json.loads(body)
    assert payload[0]["name"] == "people"
    assert payload[0]["count"] == 3


def test_collection_details(server):
    status, body = get(f"{server}/api/collections/people")
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == 3
    assert payload["indexes"][0]["name"] == "_id_"


def test_fetching_documents(server):
    status, body = get(f"{server}/api/collections/people/documents")
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == 3
    assert payload["documents"][0]["name"] == "Ada"


def test_querying_with_a_filter(server):
    status, body = get(f"{server}" + documents_url("people", '{"age": {"$gt": 30}}'))
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == 2


def test_querying_through_the_post_endpoint(server):
    status, payload = post(
        f"{server}/api/collections/people/query",
        {"filter": {"name": "Ada"}, "projection": {"name": 1, "_id": 0}},
    )
    assert status == 200
    assert payload["documents"] == [{"name": "Ada"}]


def test_sorting(server):
    status, payload = post(
        f"{server}/api/collections/people/query",
        {"filter": {}, "sort": [["age", -1]]},
    )
    assert status == 200
    assert [d["age"] for d in payload["documents"]] == [41, 36, 29]


def test_aggregating(server):
    status, payload = post(
        f"{server}/api/collections/people/aggregate",
        {"pipeline": [{"$group": {"_id": None, "total": {"$sum": "$age"}}}]},
    )
    assert status == 200
    assert payload["documents"][0]["total"] == 106


def test_inserting(server):
    status, payload = post(
        f"{server}/api/collections/people/insert",
        {"document": {"name": "Barbara", "age": 60}},
    )
    assert status == 200
    assert "$oid" in payload["insertedId"]

    status, body = get(f"{server}/api/collections/people")
    assert json.loads(body)["count"] == 4


def test_updating(server):
    status, payload = post(
        f"{server}/api/collections/people/update",
        {"filter": {"name": "Ada"}, "update": {"$set": {"age": 37}}},
    )
    assert status == 200
    assert payload["modified"] == 1

    status, body = get(f"{server}" + documents_url("people", '{"name": "Ada"}'))
    assert json.loads(body)["documents"][0]["age"] == 37


def test_deleting(server):
    status, body = get(f"{server}" + documents_url("people", '{"name": "Ada"}'))
    document_id = json.loads(body)["documents"][0]["_id"]["$oid"]

    status, payload = delete(f"{server}/api/collections/people/documents/{document_id}")
    assert status == 200
    assert payload["deleted"] == 1

    status, body = get(f"{server}/api/collections/people")
    assert json.loads(body)["count"] == 2


def test_a_bad_filter_is_reported(server):
    status, body = get(f"{server}" + documents_url("people", "{not json}"))
    assert status == 400
    assert "error" in json.loads(body)


def test_an_unknown_collection_is_reported(server):
    status, body = get(f"{server}/api/collections/nothing")
    assert status == 400
    assert "error" in json.loads(body)


def test_an_unknown_route_is_a_404(server):
    assert get(f"{server}/nowhere")[0] == 404


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
