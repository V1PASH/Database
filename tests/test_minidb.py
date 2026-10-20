"""Tests for the parts of minidb that MongoDB parity does not cover:
schemas, indexes, persistence, transactions and the storage engine.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import uuid
from decimal import Decimal

import pytest

from minidb import Database, ObjectId, ValidationError
from minidb.errors import (
    CollectionAlreadyExists,
    CollectionNotFound,
    DuplicateKeyError,
    WriteError,
)


@pytest.fixture()
def db():
    database = Database()
    yield database
    database.close()


@pytest.fixture()
def people(db):
    db.create_collection("people")
    db.people.insert_many(
        [
            {"name": "Ada", "age": 36, "tags": ["math", "code"], "city": "London"},
            {"name": "Alan", "age": 41, "tags": ["logic"], "city": "Wilmslow"},
            {"name": "Grace", "age": 29, "tags": ["code", "navy"], "city": "New York"},
            {"name": "Edsger", "age": 41, "tags": [], "city": "Rotterdam"},
        ]
    )
    return db.people


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------


def test_schema_rejects_missing_required_field(db):
    db.create_collection("users", validator={"required": ["name"]})
    with pytest.raises(ValidationError) as error:
        db.users.insert_one({"email": "a@b.com"})
    assert "name" in str(error.value)


def test_schema_checks_types(db):
    db.create_collection("users", validator={"properties": {"age": {"type": "integer"}}})
    with pytest.raises(ValidationError):
        db.users.insert_one({"age": "old"})


def test_schema_applies_defaults(db):
    db.create_collection(
        "users",
        validator={
            "properties": {
                "role": {"type": "string", "default": "member"},
                "active": {"type": "boolean", "default": True},
            }
        },
    )
    db.users.insert_one({"name": "Ada"})
    stored = db.users.find_one({})
    assert stored["role"] == "member"
    assert stored["active"] is True


def test_schema_rejects_extra_fields(db):
    db.create_collection(
        "users",
        validator={"properties": {"name": {"type": "string"}}, "additionalProperties": False},
    )
    db.users.insert_one({"name": "Ada"})
    with pytest.raises(ValidationError):
        db.users.insert_one({"name": "Ada", "sneaky": 1})


def test_schema_allows_the_id_field(db):
    db.create_collection(
        "users",
        validator={"properties": {"name": {"type": "string"}}, "additionalProperties": False},
    )
    db.users.insert_one({"name": "Ada"})


def test_schema_validates_nested_documents(db):
    db.create_collection(
        "users",
        validator={
            "properties": {
                "profile": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "minLength": 2}},
                }
            }
        },
    )
    db.users.insert_one({"profile": {"city": "Oslo"}})
    with pytest.raises(ValidationError):
        db.users.insert_one({"profile": {"city": "X"}})


def test_schema_validates_array_items(db):
    db.create_collection(
        "users",
        validator={"properties": {"tags": {"type": "array", "items": {"type": "string"}}}},
    )
    db.users.insert_one({"tags": ["a", "b"]})
    with pytest.raises(ValidationError):
        db.users.insert_one({"tags": ["a", 2]})


def test_schema_enum_and_pattern(db):
    db.create_collection(
        "users",
        validator={
            "properties": {
                "role": {"type": "string", "enum": ["admin", "member"]},
                "slug": {"type": "string", "pattern": "^[a-z-]+$"},
            }
        },
    )
    db.users.insert_one({"role": "admin", "slug": "ok-slug"})
    with pytest.raises(ValidationError):
        db.users.insert_one({"role": "root"})
    with pytest.raises(ValidationError):
        db.users.insert_one({"slug": "Not OK"})


def test_schema_can_be_replaced(db):
    db.create_collection("users", validator={"required": ["name"]})
    db.set_collection_validator("users", {"required": ["email"]})
    db.users.insert_one({"email": "a@b.com"})
    with pytest.raises(ValidationError):
        db.users.insert_one({"name": "no email"})


# ---------------------------------------------------------------------------
# indexes
# ---------------------------------------------------------------------------


def test_unique_index_rejects_duplicates(db):
    db.create_collection("users")
    db.users.create_index("email", unique=True)
    db.users.insert_one({"email": "a@b.com"})
    with pytest.raises(DuplicateKeyError):
        db.users.insert_one({"email": "a@b.com"})


def test_unique_index_is_updated_when_a_document_changes(db):
    db.create_collection("users")
    db.users.create_index("email", unique=True)
    db.users.insert_many([{"email": "a@b.com"}, {"email": "c@d.com"}])
    db.users.update_one({"email": "a@b.com"}, {"$set": {"email": "a2@b.com"}})

    # The old key is gone and the new one is searchable.
    assert db.users.find_one({"email": "a@b.com"}) is None
    assert db.users.find_one({"email": "a2@b.com"})["email"] == "a2@b.com"


def test_unique_index_blocks_an_update_that_would_collide(db):
    db.create_collection("users")
    db.users.create_index("email", unique=True)
    db.users.insert_many([{"email": "a@b.com"}, {"email": "c@d.com"}])
    with pytest.raises(DuplicateKeyError):
        db.users.update_one({"email": "a@b.com"}, {"$set": {"email": "c@d.com"}})


def test_index_backfills_existing_documents(db):
    db.create_collection("users")
    db.users.insert_many([{"email": "a@b.com"}, {"email": "c@d.com"}])
    db.users.create_index("email")
    assert db.users.find_one({"email": "c@d.com"})["email"] == "c@d.com"


def test_indexed_and_unindexed_queries_agree(db):
    """The index must never change the answer."""
    db.create_collection("things")
    db.things.insert_many(
        [
            {"n": index, "even": index % 2 == 0, "text": f"item-{index}"}
            for index in range(50)
        ]
    )

    queries = [
        {"n": 7},
        {"n": {"$gt": 40}},
        {"n": {"$gte": 10}},
        {"n": {"$lt": 10}},
        {"n": {"$lte": 10}},
        {"n": {"$in": [1, 2, 3]}},
        {"n": {"$ne": 1}},
        {"even": True},
        {"text": "item-9"},
    ]

    before = [
        (query, sorted(doc["n"] for doc in db.things.find(query))) for query in queries
    ]

    db.things.create_index("n")
    db.things.create_index("even")
    db.things.create_index("text")

    for query, expected in before:
        with_index = sorted(doc["n"] for doc in db.things.find(query))
        assert expected == with_index, query


def test_index_on_an_array_field_matches_members(people):
    people.create_index("tags")
    found = list(people.find({"tags": "code"}))
    assert sorted(document["name"] for document in found) == ["Ada", "Grace"]


def test_drop_index_removes_it(db):
    db.create_collection("things")
    db.things.create_index("n")
    assert len(db.things.list_indexes()) == 2
    db.things.drop_index("n_1")
    assert [index["name"] for index in db.things.list_indexes()] == ["_id_"]


def test_compound_index_is_named_after_its_fields(db):
    db.create_collection("things")
    name = db.things.create_index([("a", 1), ("b", 1)])
    assert name == "a_1_b_1"
    assert db.things.list_indexes()[1]["key"] == {"a": 1, "b": 1}


def test_compound_index_rejects_a_descending_direction(db):
    db.create_collection("things")
    with pytest.raises(WriteError) as error:
        db.things.create_index([("a", 1), ("b", -1)])
    assert "descending" in str(error.value)


def test_compound_index_finds_the_right_documents(db):
    db.create_collection("things")
    db.things.insert_many(
        [
            {"tenant": "a", "user": "u1"},
            {"tenant": "a", "user": "u2"},
            {"tenant": "b", "user": "u1"},
        ]
    )
    db.things.create_index([("tenant", 1), ("user", 1)])

    found = list(db.things.find({"tenant": "a", "user": "u1"}))
    assert len(found) == 1
    assert found[0]["tenant"] == "a"


def test_compound_index_uses_its_prefix(db):
    db.create_collection("things")
    db.things.insert_many(
        [{"tenant": "a", "user": "u1"}, {"tenant": "a", "user": "u2"}, {"tenant": "b"}]
    )
    db.things.create_index([("tenant", 1), ("user", 1)])

    plan = db.things.explain({"tenant": "a"})
    assert plan["strategy"] == "IXSCAN"
    assert plan["documentsExamined"] == 2


def test_compound_index_does_not_help_when_the_prefix_is_missing(db):
    db.create_collection("things")
    db.things.insert_many([{"tenant": "a", "user": "u1"}])
    db.things.create_index([("tenant", 1), ("user", 1)])

    plan = db.things.explain({"user": "u1"})
    assert plan["strategy"] == "COLLSCAN"


def test_compound_index_and_unindexed_queries_agree(db):
    """A compound index must never change the answer."""
    db.create_collection("things")
    db.things.insert_many(
        [
            {
                "tenant": ["a", "b"][index % 2],
                "user": f"u{index}",
                "n": index,
                "tags": ["x", "y"][: 1 + index % 2],
            }
            for index in range(40)
        ]
    )

    queries = [
        {"tenant": "a", "user": "u3"},
        {"tenant": "a"},
        {"tenant": "a", "n": {"$gt": 20}},
        {"tenant": "a", "user": {"$gt": "u20"}},
        {"tenant": "a", "tags": "y"},
        {"tenant": "a", "n": {"$lte": 5}},
    ]

    before = [
        (query, sorted(doc["n"] for doc in db.things.find(query))) for query in queries
    ]

    db.things.create_index([("tenant", 1), ("user", 1)])
    db.things.create_index([("tenant", 1), ("n", 1)])
    db.things.create_index([("tenant", 1), ("tags", 1)])

    for query, expected in before:
        assert sorted(doc["n"] for doc in db.things.find(query)) == expected, query


def test_unique_compound_index(db):
    db.create_collection("things")
    db.things.create_index([("tenant", 1), ("user", 1)], unique=True)
    db.things.insert_one({"tenant": "a", "user": "u1"})
    with pytest.raises(DuplicateKeyError):
        db.things.insert_one({"tenant": "a", "user": "u1"})
    # A different pair is fine.
    db.things.insert_one({"tenant": "a", "user": "u2"})
    db.things.insert_one({"tenant": "b", "user": "u1"})


def test_compound_index_with_an_array_field(db):
    db.create_collection("things")
    db.things.insert_many(
        [
            {"tenant": "a", "tags": ["x", "y"]},
            {"tenant": "a", "tags": ["y", "z"]},
            {"tenant": "b", "tags": ["x"]},
        ]
    )
    db.things.create_index([("tenant", 1), ("tags", 1)])
    found = list(db.things.find({"tenant": "a", "tags": "y"}))
    assert len(found) == 2


def test_compound_index_with_two_array_fields(db):
    db.create_collection("things")
    db.things.insert_many(
        [{"a": [1, 2], "b": [3, 4]}, {"a": [5], "b": [6]}]
    )
    db.things.create_index([("a", 1), ("b", 1)])
    assert len(list(db.things.find({"a": 2, "b": 4}))) == 1


def test_duplicate_index_path_is_rejected(db):
    db.create_collection("things")
    db.things.create_index("n")
    with pytest.raises(WriteError):
        db.things.create_index("n", name="n_other")


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def test_dotted_path_query(people):
    assert [d["name"] for d in people.find({"city": "London"})] == ["Ada"]


def test_nested_array_query(db):
    db.create_collection("orders")
    db.orders.insert_one({"customer": {"name": "Ada", "orders": [{"total": 5}, {"total": 9}]}})
    assert db.orders.count_documents({"customer.orders.total": 9}) == 1


def test_regex_query(people):
    found = list(people.find({"name": {"$regex": "^A"}}))
    assert sorted(d["name"] for d in found) == ["Ada", "Alan"]


def test_elem_match_query(db):
    db.create_collection("orders")
    db.orders.insert_many(
        [
            {"items": [{"qty": 1, "sku": "a"}, {"qty": 5, "sku": "b"}]},
            {"items": [{"qty": 2, "sku": "c"}]},
        ]
    )
    assert db.orders.count_documents({"items": {"$elemMatch": {"qty": {"$gt": 3}}}}) == 1


def test_inferred_schema_describes_the_data(db):
    db.create_collection("things")
    db.things.insert_many(
        [
            {"name": "Ada", "age": 36, "address": {"city": "London"}},
            {"name": "Alan", "tags": ["x", "y"]},
        ]
    )

    fields = {field["name"]: field for field in db.things.inferred_schema()}

    assert fields["name"]["types"] == ["string"]
    assert fields["name"]["optional"] is False       # in every document
    assert fields["age"]["optional"] is True          # only in one
    assert fields["age"]["count"] == 1
    assert fields["address.city"]["types"] == ["string"]
    assert fields["tags"]["types"] == ["array"]

def test_inferred_schema_notices_several_types(db):
    db.create_collection("things")
    db.things.insert_many([{"value": 1}, {"value": "two"}])
    fields = {field["name"]: field for field in db.things.inferred_schema()}
    assert fields["value"]["types"] == ["int", "string"]


def test_collection_stats(db):
    db.create_collection("things")
    db.things.insert_many([{"a": 1}, {"a": 2}])
    db.things.create_index("a")

    stats = db.things.stats()
    assert stats["count"] == 2
    assert stats["dataSize"] > 0
    assert stats["indexCount"] == 2          # _id_ and a_1
    assert {field["name"] for field in stats["schema"]} == {"_id", "a"}


def test_explain_reports_the_plan(db):
    db.create_collection("things")
    db.things.insert_many([{"n": index} for index in range(10)])
    db.things.create_index("n")

    plan = db.things.explain({"n": 3})
    assert plan["strategy"] == "IXSCAN"
    assert plan["index"] == "n_1"
    assert plan["documentsExamined"] == 1
    assert plan["documentsMatched"] == 1
    assert plan["totalDocuments"] == 10


def test_explain_falls_back_to_a_scan(db):
    db.create_collection("things")
    db.things.insert_many([{"n": index} for index in range(10)])
    plan = db.things.explain({"n": 3})
    assert plan["strategy"] == "COLLSCAN"
    assert plan["documentsExamined"] == 10


def test_unknown_operator_is_reported(people):
    with pytest.raises(WriteError):
        list(people.find({"age": {"$bogus": 1}}))


def test_mixing_operators_and_fields_is_reported(people):
    with pytest.raises(WriteError):
        list(people.find({"age": {"$gt": 1, "plain": 2}}))


def test_missing_field_matches_null(people):
    assert people.count_documents({"nothing_here": None}) == 4


def test_projection_inclusion_and_exclusion(people):
    included = people.find_one({"name": "Ada"}, {"name": 1, "_id": 0})
    assert included == {"name": "Ada"}

    excluded = people.find_one({"name": "Ada"}, {"tags": 0, "age": 0})
    assert "tags" not in excluded and "age" not in excluded
    assert "name" in excluded


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def test_upsert_creates_the_document(db):
    db.create_collection("things")
    db.things.update_one({"name": "Ada"}, {"$set": {"age": 1}}, upsert=True)
    assert db.things.find_one({"name": "Ada"})["age"] == 1


def test_update_with_array_filter(db):
    db.create_collection("things")
    db.things.insert_one({"items": [{"n": 1}, {"n": 2}]})
    db.things.update_one(
        {}, {"$set": {"items.$[i].n": 99}}, array_filters=[{"i.n": {"$gte": 2}}]
    )
    assert db.things.find_one({})["items"] == [{"n": 1}, {"n": 99}]


def test_array_filter_on_members_that_are_not_documents(db):
    db.create_collection("things")
    db.things.insert_one({"scores": [1, 5, 9]})
    db.things.update_one(
        {}, {"$set": {"scores.$[s]": 0}}, array_filters=[{"s": {"$gte": 5}}]
    )
    assert db.things.find_one({})["scores"] == [1, 0, 0]


def test_pull_with_an_operator_expression(db):
    db.create_collection("things")
    db.things.insert_one({"scores": [1, 5, 9]})
    db.things.update_one({}, {"$pull": {"scores": {"$gte": 5}}})
    assert db.things.find_one({})["scores"] == [1]


def test_pull_with_a_document_condition(db):
    db.create_collection("things")
    db.things.insert_one({"items": [{"n": 1}, {"n": 5}]})
    db.things.update_one({}, {"$pull": {"items": {"n": {"$gte": 5}}}})
    assert db.things.find_one({})["items"] == [{"n": 1}]


def test_cannot_change_the_id(db):
    db.create_collection("things")
    stored = db.things.insert_one({"name": "Ada"}).inserted_id
    with pytest.raises(WriteError):
        db.things.update_one({"_id": stored}, {"$set": {"_id": ObjectId()}})


def test_duplicate_id_is_rejected(db):
    db.create_collection("things")
    identifier = ObjectId()
    db.things.insert_one({"_id": identifier, "name": "Ada"})
    with pytest.raises(DuplicateKeyError):
        db.things.insert_one({"_id": identifier, "name": "Alan"})


def test_find_one_and_update_returns_before_and_after(db):
    db.create_collection("things")
    db.things.insert_one({"name": "Ada", "count": 1})
    before = db.things.find_one_and_update({"name": "Ada"}, {"$inc": {"count": 1}})
    assert before["count"] == 1
    after = db.things.find_one_and_update(
        {"name": "Ada"}, {"$inc": {"count": 1}}, return_document=True
    )
    assert after["count"] == 3


def test_find_one_and_delete(db):
    db.create_collection("things")
    db.things.insert_one({"name": "Ada"})
    removed = db.things.find_one_and_delete({"name": "Ada"})
    assert removed["name"] == "Ada"
    assert db.things.count_documents() == 0


def test_replace_swaps_the_document(db):
    db.create_collection("things")
    db.things.insert_one({"name": "Ada", "age": 1})
    db.things.replace_one({"name": "Ada"}, {"city": "London"})
    stored = db.things.find_one({})
    assert "name" not in stored and stored["city"] == "London"


# ---------------------------------------------------------------------------
# persistence and transactions
# ---------------------------------------------------------------------------


def test_data_survives_reopening(tmp_path):
    path = str(tmp_path / "app.db")
    first = Database(path)
    first.create_collection("things")
    first.things.insert_one({"name": "Ada"})
    first.close()

    second = Database(path)
    assert second.things.find_one({"name": "Ada"})["name"] == "Ada"
    assert second.list_collection_names() == ["things"]
    second.close()


OLD_SCHEMA = """
CREATE TABLE _minidb_collections (
    name TEXT PRIMARY KEY, validator TEXT, created_at REAL NOT NULL);
CREATE TABLE _minidb_documents (
    collection TEXT NOT NULL, doc_id TEXT NOT NULL, seq INTEGER NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY (collection, doc_id));
CREATE TABLE _minidb_indexes (
    collection TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
    is_unique INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (collection, name));
CREATE TABLE _minidb_index_entries (
    collection TEXT NOT NULL, index_name TEXT NOT NULL, key BLOB NOT NULL,
    doc_id TEXT NOT NULL, PRIMARY KEY (collection, index_name, key, doc_id))
    WITHOUT ROWID;
CREATE TABLE _minidb_counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""


def test_an_older_database_file_is_migrated(tmp_path):
    """Opening a file written by an earlier version upgrades it in place."""
    path = str(tmp_path / "old.db")
    connection = sqlite3.connect(path)
    connection.executescript(OLD_SCHEMA)
    connection.execute(
        "INSERT INTO _minidb_collections (name, validator, created_at) "
        "VALUES ('things', NULL, 0)"
    )
    connection.execute(
        "INSERT INTO _minidb_documents (collection, doc_id, seq, data) "
        "VALUES ('things', 'oid:1', 1, ?)",
        ('{"_id": "Ada", "email": "a@b.com"}',),
    )
    connection.execute(
        "INSERT INTO _minidb_indexes (collection, name, path, is_unique) "
        "VALUES ('things', 'email_1', 'email', 0)"
    )
    connection.commit()
    connection.close()

    db = Database(path)
    try:
        # The document and the index both survived the migration.
        assert db.things.find_one({"email": "a@b.com"})["email"] == "a@b.com"
        assert [index["name"] for index in db.things.list_indexes()] == [
            "_id_",
            "email_1",
        ]
        # And the rebuilt index really is being used.
        plan = db.things.explain({"email": "a@b.com"})
        assert plan["strategy"] == "IXSCAN"
        assert plan["documentsExamined"] == 1
    finally:
        db.close()


def test_failed_transaction_leaves_nothing_behind(db):
    db.create_collection("things")
    db.things.insert_one({"name": "Ada"})

    with pytest.raises(RuntimeError):
        with db._storage.transaction():
            db._storage.insert("things", "int:1", {"name": "Bob"})
            raise RuntimeError("boom")

    assert db.things.count_documents() == 1


def test_transaction_rolls_back_a_failed_write(db):
    db.create_collection("things")
    with pytest.raises(DuplicateKeyError):
        with db._storage.transaction():
            db._storage.insert("things", "int:1", {"name": "Bob"})
            db._storage.insert("things", "int:1", {"name": "Again"})
    assert db.things.count_documents() == 0


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------


def test_collection_lifecycle(db):
    db.create_collection("things")
    assert db.list_collection_names() == ["things"]
    with pytest.raises(CollectionAlreadyExists):
        db.create_collection("things")
    db.things.rename("stuff")
    assert db.list_collection_names() == ["stuff"]
    db.drop_collection("stuff")
    assert db.list_collection_names() == []


def test_dropping_a_missing_collection_is_harmless(db):
    db.drop_collection("never_existed")


def test_unknown_collection_raises_when_the_validator_is_read(db):
    with pytest.raises(CollectionNotFound):
        db.nothing.validator


# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


def test_objectid_is_unique_and_ordered():
    first, second = ObjectId(), ObjectId()
    assert first != second
    assert first < second or second < first
    assert len(str(first)) == 24


def test_objectid_keeps_its_timestamp():
    identifier = ObjectId()
    assert abs(
        (_dt.datetime.now(_dt.timezone.utc) - identifier.generation_time).total_seconds()
    ) < 5


def test_objectid_round_trips_through_a_string():
    identifier = ObjectId()
    assert ObjectId(str(identifier)) == identifier


def test_extended_types_round_trip(db):
    db.create_collection("things")
    moment = _dt.datetime(2026, 10, 20, 12, 0, tzinfo=_dt.timezone.utc)
    identifier = uuid.uuid4()
    db.things.insert_one(
        {
            "when": moment,
            "blob": b"hello",
            "money": Decimal("12.34"),
            "guid": identifier,
        }
    )
    stored = db.things.find_one({})
    assert stored["when"] == moment
    assert stored["blob"] == b"hello"
    assert stored["money"] == Decimal("12.34")
    assert stored["guid"] == identifier


def test_nan_cannot_be_stored(db):
    db.create_collection("things")
    with pytest.raises(Exception):
        db.things.insert_one({"value": float("nan")})


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def test_group_and_project(people):
    rows = list(
        people.aggregate(
            [
                {"$match": {"age": {"$gte": 30}}},
                {"$group": {"_id": None, "total": {"$sum": "$age"}, "n": {"$sum": 1}}},
                {"$project": {"_id": 0, "total": 1, "n": 1}},
            ]
        )
    )
    assert rows == [{"total": 118, "n": 3}]


def test_unwind(people):
    rows = list(people.aggregate([{"$unwind": "$tags"}, {"$count": "total"}]))
    assert rows == [{"total": 5}]


def test_sort_by_count(people):
    rows = list(people.aggregate([{"$sortByCount": "$city"}]))
    assert rows[0]["_id"] == "New York" or rows[0]["count"] == 1


def test_unknown_stage_is_reported(people):
    with pytest.raises(WriteError):
        list(people.aggregate([{"$bogus": {}}]))
