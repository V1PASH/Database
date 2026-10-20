"""Parity tests: minidb versus a real MongoDB server.

Every case is executed against both databases and the results are compared.
This is what keeps the "MongoDB-like" promise honest -- if MongoDB does
something surprising, these tests fail until minidb matches it.

The whole module is skipped when no MongoDB server is reachable.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest

from minidb import Database

MONGO_URI = "mongodb://127.0.0.1:27017"

pymongo = pytest.importorskip("pymongo")


@pytest.fixture(scope="module")
def mongo_client():
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    try:
        client.server_info()
    except Exception:  # pragma: no cover - depends on the environment
        pytest.skip("no MongoDB server available")
    yield client
    client.drop_database("minidb_parity")


@pytest.fixture()
def minidb_db():
    db = Database()
    db.create_collection("things")
    yield db
    db.close()


@pytest.fixture()
def mongo_collection(mongo_client):
    collection = mongo_client.minidb_parity.things
    collection.delete_many({})
    # Start from a clean set of indexes, so one test cannot leak into the next.
    for index in collection.list_indexes():
        if index["name"] != "_id_":
            collection.drop_index(index["name"])
    yield collection
    collection.delete_many({})


def canonical(value: Any) -> Any:
    """A comparable form of a document (the _id is ignored on purpose)."""
    if isinstance(value, dict):
        return tuple(sorted((key, canonical(item)) for key, item in value.items() if key != "_id"))
    if isinstance(value, (list, tuple)):
        return tuple(canonical(item) for item in value)
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if hasattr(value, "binary"):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def comparable(documents: list[dict]) -> list[str]:
    """A sorted, comparable summary of a result set."""
    return sorted(repr(canonical(document)) for document in documents)


SAMPLE = [
    {"name": "Ada", "age": 36, "score": 9.5, "tags": ["math", "code"], "city": "London"},
    {"name": "Alan", "age": 41, "score": 8, "tags": ["logic"], "city": "Wilmslow"},
    {"name": "Grace", "age": 29, "score": 9.5, "tags": ["code", "navy"], "city": "New York"},
    {"name": "Edsger", "age": 41, "score": 7.25, "tags": [], "city": "Rotterdam"},
    {"name": "Barbara", "age": None, "score": 10, "tags": ["code"], "city": "New York"},
    {"name": "Katherine", "age": 101, "score": 9.75, "tags": ["math", "navy"], "city": "Hampton"},
]


def seed(minidb_collection, mongo_collection) -> None:
    minidb_collection.insert_many([dict(document) for document in SAMPLE])
    mongo_collection.insert_many([dict(document) for document in SAMPLE])


FILTERS: list[tuple[str, dict]] = [
    ("equality", {"name": "Ada"}),
    ("not equal", {"name": {"$ne": "Ada"}}),
    ("greater than", {"age": {"$gt": 36}}),
    ("greater or equal", {"age": {"$gte": 36}}),
    ("less than", {"age": {"$lt": 36}}),
    ("less or equal", {"age": {"$lte": 36}}),
    ("in", {"age": {"$in": [29, 41]}}),
    ("not in", {"age": {"$nin": [29, 41]}}),
    ("in with strings", {"city": {"$in": ["New York", "London"]}}),
    ("exists true", {"score": {"$exists": True}}),
    ("exists false", {"score": {"$exists": False}}),
    ("regex", {"name": {"$regex": "^A"}}),
    ("regex case insensitive", {"name": {"$regex": "^a", "$options": "i"}}),
    ("all", {"tags": {"$all": ["code", "navy"]}}),
    ("size", {"tags": {"$size": 2}}),
    ("elemMatch on scalars", {"tags": {"$elemMatch": {"$eq": "code"}}}),
    ("and", {"age": {"$gte": 29}, "city": "New York"}),
    ("or", {"$or": [{"city": "London"}, {"age": {"$gt": 100}}]}),
    ("nor", {"$nor": [{"city": "London"}, {"age": {"$gt": 100}}]}),
    ("explicit and", {"$and": [{"age": {"$gte": 36}}, {"score": {"$gte": 9}}]}),
    ("dotted path", {"tags": "code"}),
    ("null matches missing", {"missing_field": None}),
    ("explicit null", {"age": None}),
    ("not operator", {"age": {"$not": {"$gt": 36}}}),
    ("mod", {"age": {"$mod": [2, 0]}}),
    ("type string", {"name": {"$type": "string"}}),
    ("type number", {"score": {"$type": "number"}}),
    ("in with array value", {"tags": {"$in": [["code", "navy"]]}}),
    ("gt across types", {"score": {"$gt": 9.6}}),
    ("boolean not confused with number", {"age": {"$ne": True}}),
    ("nested and", {"city": {"$in": ["New York"]}, "tags": "code"}),
    ("empty filter", {}),
]


@pytest.mark.parametrize("label,query", FILTERS, ids=[label for label, _ in FILTERS])
def test_filter_parity(minidb_db, mongo_collection, label, query):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    mine = list(minidb_collection.find(query))
    theirs = list(mongo_collection.find(query))

    assert comparable(mine) == comparable(theirs), label


def test_sort_parity(minidb_db, mongo_collection):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    mine = list(minidb_collection.find().sort([("age", -1)]))
    theirs = list(mongo_collection.find().sort([("age", -1)]))

    assert [canonical(d) for d in mine] == [canonical(d) for d in theirs]


def test_skip_limit_parity(minidb_db, mongo_collection):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    mine = list(minidb_collection.find().sort([("name", 1)]).skip(1).limit(2))
    theirs = list(mongo_collection.find().sort([("name", 1)]).skip(1).limit(2))

    assert comparable(mine) == comparable(theirs)


UPDATES: list[tuple[str, dict, dict]] = [
    ("set", {"name": "Ada"}, {"$set": {"city": "Cambridge"}}),
    ("unset", {"name": "Ada"}, {"$unset": {"score": ""}}),
    ("inc", {"name": "Ada"}, {"$inc": {"age": 1}}),
    ("mul", {"name": "Ada"}, {"$mul": {"score": 2}}),
    ("min", {"name": "Ada"}, {"$min": {"score": 1}}),
    ("max", {"name": "Ada"}, {"$max": {"score": 1}}),
    ("push", {"name": "Ada"}, {"$push": {"tags": "new"}}),
    ("push each", {"name": "Ada"}, {"$push": {"tags": {"$each": ["x", "y"]}}}),
    ("push each slice", {"name": "Ada"}, {"$push": {"tags": {"$each": ["x", "y"], "$slice": -2}}}),
    ("addToSet", {"name": "Ada"}, {"$addToSet": {"tags": "code"}}),
    ("addToSet new", {"name": "Ada"}, {"$addToSet": {"tags": "brand-new"}}),
    ("pop first", {"name": "Ada"}, {"$pop": {"tags": -1}}),
    ("pop last", {"name": "Ada"}, {"$pop": {"tags": 1}}),
    ("pull", {"name": "Ada"}, {"$pull": {"tags": "code"}}),
    ("pull all", {"name": "Ada"}, {"$pullAll": {"tags": ["math", "code"]}}),
    ("rename", {"name": "Ada"}, {"$rename": {"city": "town"}}),
    ("set nested", {"name": "Ada"}, {"$set": {"address.city": "Cambridge"}}),
    ("unset nested", {"name": "Ada"}, {"$unset": {"address.city": ""}}),
    ("multiple operators", {"name": "Ada"}, {"$set": {"a": 1}, "$inc": {"age": 2}}),
]


@pytest.mark.parametrize("label,query,update", UPDATES, ids=[label for label, _, _ in UPDATES])
def test_update_parity(minidb_db, mongo_collection, label, query, update):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    minidb_collection.update_one(query, update)
    mongo_collection.update_one(query, update)

    mine = minidb_collection.find_one(query)
    theirs = mongo_collection.find_one(query)

    assert canonical(mine) == canonical(theirs), label


def test_replace_parity(minidb_db, mongo_collection):
    """A replacement swaps the whole document out (the _id is kept)."""
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    original = minidb_collection.find_one({"name": "Ada"})
    mongo_original = mongo_collection.find_one({"name": "Ada"})
    replacement = {"city": "Cambridge", "age": 1}
    minidb_collection.replace_one({"name": "Ada"}, replacement)
    mongo_collection.replace_one({"name": "Ada"}, replacement)

    mine = minidb_collection.find_one({"_id": original["_id"]})
    theirs = mongo_collection.find_one({"_id": mongo_original["_id"]})

    assert canonical(mine) == canonical(theirs)


PIPELINES: list[tuple[str, list[dict]]] = [
    ("match and project", [{"$match": {"age": {"$gte": 36}}}, {"$project": {"name": 1, "_id": 0}}]),
    ("group sum", [{"$group": {"_id": None, "total": {"$sum": "$age"}}}]),
    ("group avg", [{"$group": {"_id": None, "mean": {"$avg": "$score"}}}]),
    ("group min max", [{"$group": {"_id": None, "lo": {"$min": "$age"}, "hi": {"$max": "$age"}}}]),
    (
        "group by city",
        [
            {"$group": {"_id": "$city", "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ],
    ),
    ("group push", [{"$group": {"_id": None, "names": {"$push": "$name"}}}]),
    ("sort and limit", [{"$sort": {"name": 1}}, {"$limit": 2}]),
    ("count", [{"$count": "total"}]),
    ("skip", [{"$skip": 2}, {"$project": {"name": 1, "_id": 0}}]),
    ("unwind", [{"$unwind": "$tags"}, {"$project": {"name": 1, "tags": 1, "_id": 0}}]),
    (
        "addFields",
        [{"$addFields": {"double": {"$multiply": ["$score", 2]}}}, {"$project": {"name": 1, "double": 1, "_id": 0}}],
    ),
    (
        "conditional",
        [
            {"$project": {"name": 1, "bucket": {"$cond": [{"$gte": ["$age", 36]}, "senior", "junior"]}, "_id": 0}},
        ],
    ),
    (
        "string ops",
        [{"$project": {"name": 1, "shout": {"$toUpper": "$name"}, "_id": 0}}],
    ),
    (
        "field rename via replaceRoot",
        [{"$replaceRoot": {"newRoot": {"who": "$name", "how_old": "$age"}}}],
    ),
]


@pytest.mark.parametrize("label,pipeline", PIPELINES, ids=[label for label, _ in PIPELINES])
def test_aggregation_parity(minidb_db, mongo_collection, label, pipeline):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    mine = list(minidb_collection.aggregate(pipeline))
    theirs = list(mongo_collection.aggregate(pipeline))

    assert comparable(mine) == comparable(theirs), label


def test_add_to_set_parity(minidb_db, mongo_collection):
    """$addToSet keeps the members unique (their order is not specified)."""
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    pipeline = [{"$group": {"_id": None, "tags": {"$addToSet": "$tags"}}}]
    mine = list(minidb_collection.aggregate(pipeline))[0]["tags"]
    theirs = list(mongo_collection.aggregate(pipeline))[0]["tags"]

    assert sorted(repr(canonical(value)) for value in mine) == sorted(
        repr(canonical(value)) for value in theirs
    )


def test_multikey_array_parity(minidb_db, mongo_collection):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    mine = list(minidb_collection.find({"tags": "code"}))
    theirs = list(mongo_collection.find({"tags": "code"}))

    assert comparable(mine) == comparable(theirs)


ARRAY_SAMPLES = [
    {"name": "scalars", "arr": [1, 5, 9]},
    {"name": "documents", "arr": [{"n": 1}, {"n": 5}]},
    {"name": "strings", "arr": ["abc", "def"]},
    {"name": "with-null", "arr": [None, 3]},
]


@pytest.mark.parametrize(
    "query",
    [
        {"arr": {"$elemMatch": {"$gte": 5}}},
        {"arr": {"$elemMatch": {"$lt": 5}}},
        {"arr": {"$elemMatch": {"$eq": 5}}},
    ],
)
def test_elem_match_type_bracketing_parity(minidb_db, mongo_collection, query):
    """$elemMatch only compares members of the same type as the operand."""
    minidb_collection = minidb_db.things
    minidb_collection.insert_many([dict(document) for document in ARRAY_SAMPLES])
    mongo_collection.insert_many([dict(document) for document in ARRAY_SAMPLES])

    mine = list(minidb_collection.find(query))
    theirs = list(mongo_collection.find(query))

    assert comparable(mine) == comparable(theirs)


def test_pull_with_operator_parity(minidb_db, mongo_collection):
    """$pull with an operator expression removes the matching members."""
    minidb_collection = minidb_db.things
    minidb_collection.insert_many([dict(document) for document in ARRAY_SAMPLES])
    mongo_collection.insert_many([dict(document) for document in ARRAY_SAMPLES])

    minidb_collection.update_many({}, {"$pull": {"arr": {"$gte": 5}}})
    mongo_collection.update_many({}, {"$pull": {"arr": {"$gte": 5}}})

    mine = list(minidb_collection.find().sort([("name", 1)]))
    theirs = list(mongo_collection.find().sort([("name", 1)]))

    assert comparable(mine) == comparable(theirs)


def test_array_filter_parity(minidb_db, mongo_collection):
    """arrayFilters update the members that match the filter."""
    documents = [
        {"name": "numbers", "items": [{"n": 1}, {"n": 5}, {"n": 9}]},
        {"name": "scalars", "items": [1, 5, 9]},
    ]
    minidb_collection = minidb_db.things
    minidb_collection.insert_many([dict(document) for document in documents])
    mongo_collection.insert_many([dict(document) for document in documents])

    minidb_collection.update_many(
        {}, {"$set": {"items.$[i].n": 0}}, array_filters=[{"i.n": {"$gte": 5}}]
    )
    mongo_collection.update_many(
        {}, {"$set": {"items.$[i].n": 0}}, array_filters=[{"i.n": {"$gte": 5}}]
    )

    mine = list(minidb_collection.find().sort([("name", 1)]))
    theirs = list(mongo_collection.find().sort([("name", 1)]))

    assert comparable(mine) == comparable(theirs)


COMPOUND_SAMPLES = [
    {"tenant": "a", "user": "u1", "n": 1, "tags": ["x"]},
    {"tenant": "a", "user": "u2", "n": 5, "tags": ["x", "y"]},
    {"tenant": "a", "user": "u3", "n": 9, "tags": ["y"]},
    {"tenant": "b", "user": "u1", "n": 2, "tags": ["y", "z"]},
    {"tenant": "b", "user": "u2", "n": 7, "tags": []},
]

COMPOUND_QUERIES = [
    {"tenant": "a", "user": "u1"},
    {"tenant": "a", "user": {"$gt": "u1"}},
    {"tenant": "a"},
    {"tenant": "b", "n": {"$gt": 3}},
    {"tenant": "a", "tags": "y"},
    {"tenant": "a", "user": "u2", "n": {"$lte": 5}},
    {"user": "u1"},
]


@pytest.mark.parametrize(
    "query", COMPOUND_QUERIES, ids=[str(query) for query in COMPOUND_QUERIES]
)
def test_compound_index_parity(minidb_db, mongo_collection, query):
    """A compound index must not change the answers MongoDB gives."""
    minidb_collection = minidb_db.things
    minidb_collection.insert_many([dict(d) for d in COMPOUND_SAMPLES])
    mongo_collection.insert_many([dict(d) for d in COMPOUND_SAMPLES])

    minidb_collection.create_index([("tenant", 1), ("user", 1)])
    minidb_collection.create_index([("tenant", 1), ("n", 1)])
    minidb_collection.create_index([("tenant", 1), ("tags", 1)])
    mongo_collection.create_index([("tenant", 1), ("user", 1)])
    mongo_collection.create_index([("tenant", 1), ("n", 1)])
    mongo_collection.create_index([("tenant", 1), ("tags", 1)])

    mine = list(minidb_collection.find(query))
    theirs = list(mongo_collection.find(query))

    assert comparable(mine) == comparable(theirs)


def test_compound_unique_index_parity(minidb_db, mongo_collection):
    """A unique compound index rejects the same duplicates MongoDB does."""
    minidb_collection = minidb_db.things
    minidb_collection.create_index([("tenant", 1), ("user", 1)], unique=True)
    mongo_collection.create_index([("tenant", 1), ("user", 1)], unique=True)

    document = {"tenant": "a", "user": "u1"}
    minidb_collection.insert_one(dict(document))
    mongo_collection.insert_one(dict(document))

    with pytest.raises(Exception):
        minidb_collection.insert_one(dict(document))
    with pytest.raises(Exception):
        mongo_collection.insert_one(dict(document))


def test_distinct_parity(minidb_db, mongo_collection):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    assert sorted(minidb_collection.distinct("city")) == sorted(
        mongo_collection.distinct("city")
    )
    assert sorted(minidb_collection.distinct("tags")) == sorted(
        mongo_collection.distinct("tags")
    )


def test_count_documents_parity(minidb_db, mongo_collection):
    minidb_collection = minidb_db.things
    seed(minidb_collection, mongo_collection)

    for query in ({"city": "New York"}, {"age": {"$gt": 30}}, {"tags": "code"}):
        assert minidb_collection.count_documents(query) == mongo_collection.count_documents(query)
