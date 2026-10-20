"""A small benchmark: how much does an index actually buy you?

    python -m bench.benchmark
"""

from __future__ import annotations

import random
import time

from minidb import Database

DOCUMENT_COUNT = 20_000


def build(db: Database) -> None:
    db.create_collection("people")
    random.seed(7)

    people = []
    for index in range(DOCUMENT_COUNT):
        people.append(
            {
                "name": f"person-{index}",
                "email": f"person-{index}@example.com",
                "age": random.randint(18, 80),
                "city": random.choice(["London", "Oslo", "Nairobi", "Tokyo", "Lima"]),
                "tags": random.sample(["a", "b", "c", "d"], k=2),
            }
        )

    db.people.insert_many(people)


def time_it(label: str, function, repeats: int = 5) -> float:
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        best = min(best, time.perf_counter() - start)
    print(f"  {label:<46} {best * 1000:8.2f} ms")
    return best


def main() -> None:
    db = Database(":memory:")
    build(db)

    print(f"\n{DOCUMENT_COUNT:,} documents\n")

    queries = {
        "exact match on email": {"email": "person-12345@example.com"},
        "range on age": {"age": {"$gte": 70}},
        "match on city": {"city": "Tokyo"},
        "array member": {"tags": "c"},
    }

    print("without an index")
    before = {}
    for label, query in queries.items():
        before[label] = time_it(label, lambda q=query: list(db.people.find(q)))

    print("\nwith an index on email, age, city and tags")
    db.people.create_index("email")
    db.people.create_index("age")
    db.people.create_index("city")
    db.people.create_index("tags")

    for label, query in queries.items():
        time_it(label, lambda q=query: list(db.people.find(q)))

    print("\nwhat the planner does")
    for label, query in queries.items():
        plan = db.people.explain(query)
        print(
            f"  {label:<46} {plan['strategy']:<9} "
            f"examined {plan['documentsExamined']:>6} of {plan['totalDocuments']}"
        )

    print("\nthe same question asked of a compound index")

    db.create_collection("orders")
    db.orders.insert_many(
        [
            {
                "customer": f"person-{index}",
                "region": ["north", "south", "east"][index % 3],
                "total": index * 3,
                "paid": index % 2 == 0,
            }
            for index in range(DOCUMENT_COUNT)
        ]
    )

    compound_queries = {
        "customer + region (both pinned)": {
            "customer": "person-500",
            "region": "south",
        },
        "customer only (prefix)": {"customer": "person-500"},
        "customer + total range": {
            "customer": "person-500",
            "total": {"$gte": 1000},
        },
    }

    for label, query in compound_queries.items():
        time_it(label, lambda q=query: list(db.orders.find(q)))

    db.orders.create_index([("customer", 1), ("region", 1)])
    db.orders.create_index([("customer", 1), ("total", 1)])

    print()
    for label, query in compound_queries.items():
        time_it(label, lambda q=query: list(db.orders.find(q)))
        plan = db.orders.explain(query)
        print(
            f"  {label:<46} {plan['strategy']:<9} "
            f"examined {plan['documentsExamined']:>6} of {plan['totalDocuments']}"
            f"   via {plan['index']}"
        )

    db.close()


if __name__ == "__main__":
    main()
