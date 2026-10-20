# minidb

[![CI](https://github.com/V1PASH/Database/actions/workflows/ci.yml/badge.svg)](https://github.com/V1PASH/Database/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**A MongoDB-style document database, written from scratch.**

You asked for a database of your own that behaves like MongoDB. This is one:
a document store with a real query engine, its own index structures, its own
aggregation pipeline and schema validation -- built on top of SQLite for
durability, but with none of MongoDB's behaviour handed to you for free.

The API is deliberately shaped like PyMongo's, so application code written
against minidb keeps working if you ever point it at a real MongoDB server.

```python
from minidb import Database

db = Database("app.db")
db.create_collection("users", validator={"required": ["name", "email"]})

db.users.insert_one({"name": "Ada", "email": "ada@example.com", "role": "admin"})
db.users.find_one({"email": "ada@example.com"})
db.users.update_one({"name": "Ada"}, {"$inc": {"logins": 1}})
db.users.create_index("email", unique=True)
```

---

## Is it really MongoDB-compatible?

Rather than guess, minidb is tested **against a real MongoDB server**. Every
filter, update and pipeline in `tests/test_parity.py` is executed against
both databases and the results compared:

```
$ pytest tests/ -q
163 passed
```

That suite is what pinned down the subtle parts of MongoDB's behaviour --
for example, that range operators *type bracket* (a `null` field never
matches `{"age": {"$lt": 36}}`), that `$min`/`$max` ignore nulls, and that
`$addToSet` ordering is deliberately unspecified.

If you have MongoDB running locally, the parity tests use it. Without it,
they skip and the rest of the suite still runs.

---

## Getting started

```bash
pip install minidb-db            # from PyPI
pip install "minidb-db[django]"  # with Django, for the integration
pip install -e ".[dev]"          # from a clone, with the test tools
```

Then:

```bash
python -m app.demo          # a guided tour of the example app
python -m bench.benchmark   # what indexes buy you
pytest tests/ -q            # the test suite
minidb-ui app.db            # browse the database in your browser
```

The import name is `minidb`, whatever the package is called:

```python
from minidb import Database

db = Database("app.db")
db.users.insert_one({"name": "Ada", "email": "ada@example.com"})
```

The package has **no runtime dependencies** -- it is pure Python on top of
the standard library's `sqlite3`.

---

## What is in the box

| Area | What works |
|---|---|
| **Documents** | JSON documents with `ObjectId`, `datetime`, `Decimal`, `UUID` and `bytes` support |
| **Queries** | `$eq` `$ne` `$gt` `$gte` `$lt` `$lte` `$in` `$nin` `$exists` `$regex` `$options` `$all` `$size` `$elemMatch` `$not` `$type` `$mod`, plus `$and` `$or` `$nor` |
| **Paths** | Dotted paths (`"address.city"`), implicit traversal into arrays |
| **Updates** | `$set` `$unset` `$inc` `$mul` `$min` `$max` `$push` `$addToSet` `$pop` `$pull` `$pullAll` `$rename` `$setOnInsert` `$currentDate`, `arrayFilters`, upserts, replacements |
| **Aggregation** | `$match` `$project` `$addFields`/`$set` `$unset` `$group` `$sort` `$limit` `$skip` `$count` `$unwind` `$replaceRoot` `$sortByCount` and ~30 expressions |
| **Schemas** | Per-collection validators: types, `required`, `enum`, `pattern`, numeric bounds, array rules, `additionalProperties`, and **`default` values** |
| **Indexes** | Single-field and **compound** indexes (unique or not), multikey (array) indexes, order-preserving keys that serve equality *and* range lookups |
| **Writes** | Transactions with rollback, unique-constraint enforcement, `ObjectId` generation |
| **Extras** | `explain()` to see the query plan, `distinct()`, `count_documents()`, cursors with `sort`/`skip`/`limit` |

---

## How it works

```
            your app
               |
        minidb/collection.py     the API (PyMongo shaped)
               |
    +----------+-----------+-------------------+
    |          |           |                   |
 query.py  update.py  aggregation.py    validation.py
    |          |           |                   |
    +----------+-----------+-------------------+
               |
          index.py         the planner + the key encoding
               |
         engine.py          SQLite: documents, index tables, transactions
               |
           SQLite file
```

**Storage.** Documents are stored as JSON text, one row per document, keyed
by `(collection, id)`. SQLite gives durability, atomicity and crash safety
for free -- but every decision that defines *how the database behaves*
lives in this project, not in SQLite.

**Indexes.** minidb keeps its own index tables (`_minidb_index_entries`),
keyed by `(collection, index, encoded_key, document_id)` and stored
`WITHOUT ROWID`, so SQLite's B-tree does the work. The encoding is
**order preserving**: a value is mapped onto bytes whose byte order matches
MongoDB's BSON comparison order, which is what lets one index serve both
equality lookups and range scans. Each component of a key is
self-delimiting, so a compound key's components are compared one after the
other.

**The planner.** `index.py` looks at the filter, finds an indexed field it
can use, and hands the engine a plan (`eq`, `prefix` or `range`). The engine
then returns a *candidate set* -- always a **superset** of the matching
documents -- and the full filter is applied in Python afterwards. That
last step is what keeps the answers correct: an index may be an
approximation, but it can never change the result.

**Type ordering.** MongoDB compares values of different types using a fixed
order (null < numbers < strings < objects < arrays < ... < dates). minidb
reproduces that in `comparison.py`, which is why `{"a": {"$gt": 5}}` behaves
the same in both.

---

## Using it

### Reading

```python
db.users.find_one({"email": "ada@example.com"})
db.users.find({"age": {"$gte": 30}}, sort=[("age", -1)], limit=10)
db.users.find({"address.city": "London"}).sort("name").skip(20)

db.users.find_one({"name": "Ada"}, {"name": 1, "email": 1, "_id": 0})
```

### Writing

```python
db.users.insert_one({"name": "Ada", "email": "ada@example.com"})
db.users.insert_many([...])

db.users.update_one({"_id": oid}, {"$set": {"role": "admin"}, "$inc": {"logins": 1}})
db.users.update_many({"role": "viewer"}, {"$set": {"active": False}})
db.users.replace_one({"_id": oid}, {"name": "Ada", "email": "a@b.com"})

db.users.update_one(
    {"_id": oid},
    {"$set": {"items.$[i].done": True}},
    array_filters=[{"i.qty": {"$gte": 2}}],
)
```

### Aggregating

```python
list(db.tasks.aggregate([
    {"$match": {"project_id": project_id}},
    {"$group": {"_id": "$assignee_id", "hours": {"$sum": "$estimate_hours"}}},
    {"$sort": {"hours": -1}},
]))
```

### Schemas

```python
db.create_collection("users", validator={
    "required": ["name", "email"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "email": {"type": "string", "format": "email"},
        "role": {"type": "string", "enum": ["admin", "member"], "default": "member"},
    },
})
```

A document that breaks the schema is rejected on write, with a message
pointing at the offending field. `default` values are filled in for you.

### Indexes

```python
db.users.create_index("email", unique=True)
db.tasks.create_index([("project_id", 1), ("status", 1)])   # compound
db.users.list_indexes()
db.users.explain({"email": "ada@example.com"})
```

```
{'strategy': 'IXSCAN', 'index': 'email_1', 'documentsExamined': 1, 'totalDocuments': 20000}
```

A **compound index** covers several fields. Its key is the concatenation of
self-delimiting component keys, so the components are compared one after
the other -- exactly like MongoDB, whose compound keys are compared
component by component. That is what lets one index answer:

| Query | What the index does |
|---|---|
| `{"a": 1, "b": 2}` | exact key lookup |
| `{"a": 1}` | prefix scan |
| `{"a": 1, "b": {"$gt": 5}}` | one contiguous range of keys |
| `{"b": 2}` | not usable -- `a` comes first, so the index cannot help |

On 20,000 documents, a compound index turned a 55.9 ms scan into a
0.01 ms lookup.

---

## The web UI

Every database deserves a way to look at it. minidb ships with one -- a
browser app in the spirit of MongoDB Compass:

```bash
minidb-ui                      # every database file in this directory
minidb-ui ~/databases          # every database file in here
minidb-ui app.db               # just this one
```

No data yet? Seed a sample database first:

```bash
python -c "from minidb import Database; from minidb.demo import seed; seed(Database('demo.db'))"
minidb-ui
```

<img src="docs/ui.png" alt="The minidb web UI" width="100%">

It opens on **http://127.0.0.1:5433** and shows every database it can find
as a tree, the way Compass shows a server:

```
DATABASES
  shop       4 collections
    users
    projects
  notes      1 collection
    ideas
```

- **Browse** -- every collection with its document count, its indexes, and a
  filter/sort/limit toolbar
- **Query** -- a filter, a projection, a sort, a limit and a skip
- **Aggregate** -- a whole aggregation pipeline
- **Insert** -- a document, validated by the collection's schema
- **Info** -- statistics (documents, data size, indexes, fields), the
  **schema inferred from the data** (every field, its type, and how often it
  is present), the index list and the collection's validator
- **Explain** -- the query plan for the current filter: which index was
  used, how many documents were examined, how many matched
- **Document detail** -- click any document for highlighted JSON, and delete
  it from there

Deep links work too: `?database=shop&collection=users&tab=info` opens
straight there.

### Finding the databases

minidb is embedded, so there is no server holding the databases -- each one
is a file. The browser finds them by:

- **scanning a directory** for `*.db`, `*.sqlite`, `*.sqlite3` and
  `*.minidb` files (the current directory by default)
- **remembering** the ones you have opened, in `~/.minidb/workspace.json`
- the **"+ open"** button, which takes a path to any database file

The server is built on `http.server` from the standard library, so it has no
dependencies of its own. It binds to localhost and has **no authentication**:
it is a local development tool, not something to expose to a network.

### Which port?

**minidb itself has no port.** It is an *embedded* database, like SQLite: it
lives inside your Python process and is reached through function calls, not
through a socket. There is no server to connect to, and no port to open.

The only network port is the GUI's, and it defaults to **5433**:

```bash
minidb-ui app.db --port 9000     # change it
minidb-ui app.db --host 0.0.0.0  # bind elsewhere (see the warning above)
```

---

## Django

minidb comes with a Django integration, so a Django project can keep its
data in minidb.

```python
# settings.py
MINIDB_PATH = "app.db"
```

Models that inherit from `MinidbModel` are stored in minidb, one document
per row, with the Django primary key as the document's `_id`:

```python
from django.db import models
from minidb.django import MinidbModel

class Article(MinidbModel):
    title = models.CharField(max_length=200)
    views = models.IntegerField(default=0)
    published = models.BooleanField(default=False)

    class Meta:
        app_label = "blog"
```

The queryset API works as usual, because Django's *lookups* are translated
into minidb's query language rather than into SQL:

```python
Article.objects.create(title="Hello", views=12, published=True)

Article.objects.filter(published=True)          # -> {"published": true}
Article.objects.filter(views__gt=10)             # -> {"views": {"$gt": 10}}
Article.objects.filter(title__icontains="hel")   # -> a regular expression
Article.objects.filter(title__in=["a", "b"])     # -> {"$in": [...]}
Article.objects.exclude(published=True)          # -> {"$nor": [...]}
Article.objects.filter(Q(a=1) | Q(b=2))          # -> {"$or": [...]}
Article.objects.order_by("-views")
Article.objects.values("title", "views")
Article.objects.values_list("title", flat=True)
```

Three of Django's pluggable interfaces are backed by minidb as well:

```python
# settings.py
SESSION_ENGINE = "minidb.django.sessions"
CACHES = {"default": {"BACKEND": "minidb.django.cache.MinidbCache"}}
AUTHENTICATION_BACKENDS = ["minidb.django.auth.MinidbUserBackend"]
```

There is a working example project in `django_example/`:

```bash
cd django_example
python manage.py migrate      # Django's own tables
python manage.py runserver     # http://127.0.0.1:8000/
```

<img src="docs/django.png" alt="A Django site reading from minidb" width="100%">

### What this is not

**It is not a Django database backend.** Django's ORM speaks SQL, and
minidb does not, so `DATABASES` still points at a relational database for
Django's own tables (migrations, the content types table, the admin site).
Models that inherit from `MinidbModel` live in minidb; models that inherit
from `models.Model` are stored by Django as usual.

The parts of the ORM that only exist to build SQL are refused with a clear
error rather than failing quietly: `annotate()`, `select_related()` and
`prefetch_related()` raise `NotSupportedError`. Aggregations, joins and
sub-queries are the things to reach for minidb's own query language for.

---

**In one SQLite file, at the path you give it.** minidb is an *embedded*
database, like SQLite: it lives inside your Python process and is reached
through function calls, not through a socket. There is no server, no port
and no daemon.

```python
db = Database("app.db")          # ./app.db, relative to the working directory
db = Database("/var/data/app.db")  # an absolute path
db = Database(":memory:")        # in memory only -- gone when the process exits
```

**Yes, it is permanent.** It survives the process exiting, and it survives
the process being killed outright -- the test suite proves it by sending
`SIGKILL` to a process that had just written 200 documents, and all 200 are
still there afterwards.

The file is a plain SQLite database, so anything that reads SQLite can read
it:

```
$ sqlite3 app.db ".tables"
_minidb_collections  _minidb_documents  _minidb_index_entries  _minidb_indexes ...
```

### One gotcha: relative paths

A relative path is resolved against the **current working directory**, so
the same line of code can open two different databases:

```python
Database("shop.db")     # in /project -> /project/shop.db
                        # in /tmp     -> /tmp/shop.db
```

Use an absolute path if that matters.

### Backups

While the database is open, SQLite keeps recent writes in a write-ahead log
next to the main file (`app.db-wal`), so **copying `app.db` on its own can
lose the last few writes.** Use the built-in backup instead -- it takes a
consistent snapshot, even while the database is being written to:

```python
db.backup("app-backup.db")     # safe to call while the database is in use
```

Copying the file by hand is fine once the database is closed.

---

## The example application

`app/` is a small but complete backend built on the database:

| File | What it holds |
|---|---|
| `schemas/collections.py` | The schemas for `users`, `projects`, `tasks`, `notes` |
| `app/database.py` | Opening the database, creating collections and indexes |
| `app/repositories.py` | The access layer the application actually uses |
| `app/demo.py` | A runnable tour (`python -m app.demo`) |

Keeping every database call inside the repositories means the storage
engine can be swapped later without touching the rest of the app -- swap
`minidb.Database` for `pymongo.MongoClient` and the repositories keep
working.

---

## Differences from MongoDB (the honest list)

minidb is a from-scratch implementation, so some things are missing or
behave differently. Everything here is a deliberate limitation, not a bug:

**Not implemented**

- Descending index directions -- compound indexes are ascending only, and
  `create_index` says so rather than quietly ignoring the direction.
- `$lookup`, `$facet`, `$out`, `$merge`, `$graphLookup`, `$unionWith`.
- The positional `$` operator in updates (`"items.$.qty"`). `arrayFilters`
  (`"items.$[i].qty"`) do work.
- Change streams, geospatial queries, full-text search, map/reduce.
- A network protocol: minidb is an embedded library, not a server.
- Replication, sharding, and transactions spanning collections.

**Different on purpose**

- `find()` returns documents in insertion order, and sorting ties keep
  insertion order. MongoDB does not promise an order for ties.
- `$addToSet` keeps insertion order; MongoDB's order is unspecified.
- `additionalProperties: False` exempts `_id`, which minidb adds itself.
- The `_id` index is always present; there is no way to drop it.
- A document with several very large array fields in one compound index
  needs too many key combinations to index completely. minidb keeps the
  document findable through an overflow table rather than hiding it.

---

## Continuous integration

Every push runs the suite on Python 3.11, 3.12 and 3.13 -- against a real
MongoDB service, so the parity tests actually run rather than skipping
themselves. The browser code is checked under node, and the package is
built, checked with `twine` and installed into a clean environment.

Releasing to PyPI is one tag away, once the repository is registered as a
trusted publisher on PyPI:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

---

## Where to take it next

The interesting problems in a database are all still open here:

1. **Descending index directions** -- a compound key whose components can
   each be scanned backwards, and a planner that uses them for reverse sorts.
2. **A real storage engine** -- replace the SQLite file with a write-ahead
   log, free-space reuse and page-level compaction.
3. **Concurrency** -- MVCC with snapshot isolation instead of a single
   write lock.
4. **A server** -- a wire protocol and a client library, so minidb can be
   used from another language.
5. **A better planner** -- cost estimates, index intersection, and choosing
   between several usable indexes.
6. **Full-text search** -- an inverted index over tokenised documents.

---

## Layout

```
minidb/          the database
  collection.py    the API
  query.py         filter semantics
  update.py        update operators
  aggregation.py   the pipeline
  validation.py    schemas
  schema.py        inferring a schema from the data
  index.py         key encoding + planner
  engine.py        SQLite storage layer
  comparison.py    BSON value ordering
  bson.py          document codec
  objectid.py      ObjectId
  database.py      the Database object
  demo.py          sample data
  webui/           the web UI (stdlib only)
    server.py        the HTTP API
    workspace.py     finding and holding the database files
    static/          the browser app
schemas/         the example app's schemas
app/             the example app
bench/           benchmarks
docs/            the screenshot
tests/
  test_minidb.py    unit tests
  test_parity.py    minidb vs. real MongoDB
  test_webui.py     the web UI's API
  frontend_smoke.mjs  the browser code, in a DOM shim
```
