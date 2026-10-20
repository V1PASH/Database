"""A runnable walk through the database.

    python -m app.demo
"""

from __future__ import annotations

from app.database import connect
from app.repositories import (
    NoteRepository,
    ProjectRepository,
    TaskRepository,
    UserRepository,
)


def main() -> None:
    db = connect(":memory:")
    users = UserRepository(db)
    projects = ProjectRepository(db)
    tasks = TaskRepository(db)
    notes = NoteRepository(db)

    print("== creating a user ==")
    ada = users.create("Ada Lovelace", "ada@example.com", age=36, role="admin")
    print("  id:", ada["_id"], "| role:", ada["role"], "| active:", ada["active"])

    alan = users.create("Alan Turing", "alan@example.com", age=41)
    print("  id:", alan["_id"], "| role:", alan["role"], "| active:", alan["active"])

    print("\n== the schema rejects bad data ==")
    try:
        users.create("Nobody", "not-an-email")
    except ValueError as exc:
        print("  rejected:", exc)

    try:
        users.create("Duplicate", "ada@example.com")
    except ValueError as exc:
        print("  rejected:", exc)

    print("\n== a project and some tasks ==")
    project = projects.create(
        "Analytical Engine",
        slug="analytical-engine",
        owner_id=ada["_id"],
        budget=5000,
        tags=["history", "computing"],
    )
    print("  project:", project["name"], "| status:", project["status"])

    tasks.create("Write the notes", project["_id"], assignee_id=alan["_id"], estimate_hours=8, priority=2)
    tasks.create("Build the mill", project["_id"], assignee_id=alan["_id"], estimate_hours=20, priority=1)
    tasks.create("Order parts", project["_id"], estimate_hours=3)

    for task in tasks.list_for_project(project["_id"]):
        print(f"  [{task['priority']}] {task['title']} ({task['status']})")

    print("\n== updating ==")
    tasks.complete(tasks.list_for_project(project["_id"])[0]["_id"])
    tasks.add_label(tasks.list_for_project(project["_id"])[0]["_id"], "urgent")
    tasks.add_checklist_item(tasks.list_for_project(project["_id"])[0]["_id"], "proofread")
    print("  status breakdown:", tasks.status_breakdown())

    print("\n== querying ==")
    print("  adults:", [u["name"] for u in users.list() if u.get("age", 0) >= 40])
    print("  tasks tagged urgent:", len(tasks.list_for_project(project["_id"])))
    print("  total estimate:", tasks.estimate_total(project["_id"]))
    print("  roles in use:", users.roles())
    print("  budget report:", projects.budget_report())

    print("\n== notes ==")
    notes.create("Remember the punch cards", project_id=project["_id"], pinned=True)
    notes.create("Mill #3 needs a new cam", project_id=project["_id"])
    print("  pinned:", [n["body"] for n in notes.pinned()])

    print("\n== indexes ==")
    for index in db.tasks.list_indexes():
        print(" ", index)

    print("\n== the compound index at work ==")
    plan = db.tasks.explain({"project_id": project["_id"], "status": "todo"})
    print(f"  strategy: {plan['strategy']} via {plan['index']}")
    print(
        f"  examined {plan['documentsExamined']} of {plan['totalDocuments']} documents "
        f"to match {plan['documentsMatched']}"
    )

    db.close()


if __name__ == "__main__":
    main()
