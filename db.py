import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "noted.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    title                       TEXT NOT NULL,
    description                 TEXT,
    due_date                    TEXT,
    due_time                    TEXT,
    priority                    TEXT NOT NULL DEFAULT 'Medium' CHECK (priority IN ('Low','Medium','High')),
    category                    TEXT,
    tags                        TEXT,
    estimated_duration_minutes  INTEGER,
    status                      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed')),
    parent_id                   INTEGER REFERENCES tasks(id),
    recurrence                  TEXT CHECK (recurrence IS NULL OR recurrence IN ('daily','weekdays','weekly','monthly')),
    reschedule_count            INTEGER NOT NULL DEFAULT 0,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);

CREATE TABLE IF NOT EXISTS suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

# Columns added after the original Phase 1 schema — applied defensively so an
# existing pre-Phase-2 noted.db upgrades in place instead of needing a fresh file.
MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN parent_id INTEGER REFERENCES tasks(id)",
    "ALTER TABLE tasks ADD COLUMN recurrence TEXT",
    "ALTER TABLE tasks ADD COLUMN reschedule_count INTEGER NOT NULL DEFAULT 0",
]

EDITABLE_FIELDS = {
    "title",
    "description",
    "due_date",
    "due_time",
    "priority",
    "category",
    "tags",
    "estimated_duration_minutes",
    "parent_id",
    "recurrence",
}


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d["tags"]) if d["tags"] else []
    return d


# ---------- tasks ----------


def create_task(**fields) -> dict:
    title = fields.get("title")
    if not title:
        raise ValueError("title is required")

    values = {k: fields.get(k) for k in EDITABLE_FIELDS}
    values["tags"] = json.dumps(values["tags"]) if values.get("tags") else None
    values["priority"] = values.get("priority") or "Medium"
    now = _now()

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks
                (title, description, due_date, due_time, priority, category,
                 tags, estimated_duration_minutes, parent_id, recurrence,
                 status, created_at, updated_at)
            VALUES (:title, :description, :due_date, :due_time, :priority, :category,
                    :tags, :estimated_duration_minutes, :parent_id, :recurrence,
                    'pending', :created_at, :updated_at)
            """,
            {**values, "created_at": now, "updated_at": now},
        )
        new_id = cur.lastrowid

    return get_task(new_id)


def create_subtasks(parent_id: int, subtasks: list[dict]) -> list[dict]:
    return [create_task(parent_id=parent_id, **s) for s in subtasks]


def get_task(task_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_subtasks(parent_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks WHERE parent_id = ?
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id
            """,
            (parent_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_tasks(status: str | None = None, top_level_only: bool = True) -> list[dict]:
    conditions = []
    params: list = []
    if top_level_only:
        conditions.append("parent_id IS NULL")
    if status:
        conditions.append("status = ?")
        params.append(status)

    query = "SELECT * FROM tasks"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += """
        ORDER BY
            CASE status WHEN 'pending' THEN 0 ELSE 1 END,
            CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
            due_date ASC,
            CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END
    """
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        tasks = [_row_to_dict(r) for r in rows]

    if top_level_only:
        for t in tasks:
            t["subtasks"] = list_subtasks(t["id"])
            t["suggestions"] = list_suggestions(t["id"])

    return tasks


def update_task(task_id: int, **fields) -> dict | None:
    current = get_task(task_id)
    if current is None:
        return None

    updates = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS and v is not None}
    if not updates:
        return current

    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"])

    if "due_date" in updates and updates["due_date"] != current.get("due_date"):
        updates["reschedule_count"] = current.get("reschedule_count", 0) + 1

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)

    with _connect() as conn:
        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = :id",
            {**updates, "id": task_id},
        )
    return get_task(task_id)


def set_status(task_id: int, status: str) -> dict | None:
    if status not in ("pending", "completed"):
        raise ValueError("status must be 'pending' or 'completed'")
    with _connect() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), task_id),
        )
    return get_task(task_id)


def _next_occurrence_date(due_date: str | None, recurrence: str) -> str:
    base = date.fromisoformat(due_date) if due_date else date.today()

    if recurrence == "weekly":
        nxt = base + timedelta(days=7)
    elif recurrence == "monthly":
        year = base.year + (base.month // 12)
        month = base.month % 12 + 1
        last_day = calendar.monthrange(year, month)[1]
        nxt = date(year, month, min(base.day, last_day))
    else:  # daily / weekdays
        nxt = base + timedelta(days=1)
        if recurrence == "weekdays":
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)

    return nxt.isoformat()


def complete_task_with_recurrence(task_id: int) -> tuple[dict | None, dict | None]:
    """Marks a task completed; if it's recurring, also creates the next occurrence.

    Shared by both the direct checkbox path and the AI complete_task tool so
    recurrence behaves identically no matter how completion was triggered.
    """
    task = get_task(task_id)
    if task is None:
        return None, None

    completed = set_status(task_id, "completed")

    new_task = None
    if task.get("recurrence"):
        new_task = create_task(
            title=task["title"],
            description=task.get("description"),
            due_date=_next_occurrence_date(task.get("due_date"), task["recurrence"]),
            due_time=task.get("due_time"),
            priority=task.get("priority"),
            category=task.get("category"),
            tags=task.get("tags") or None,
            estimated_duration_minutes=task.get("estimated_duration_minutes"),
            recurrence=task.get("recurrence"),
            parent_id=task.get("parent_id"),
        )

    return completed, new_task


def delete_task(task_id: int) -> dict | None:
    task = get_task(task_id)
    if task is None:
        return None
    with _connect() as conn:
        # Cascade: subtasks and everyone's suggestions go with the parent.
        # Note: subtasks removed this way aren't captured in the returned
        # snapshot, so undoing a parent delete won't bring its subtasks back.
        child_ids = [r["id"] for r in conn.execute("SELECT id FROM tasks WHERE parent_id = ?", (task_id,)).fetchall()]
        for cid in child_ids:
            conn.execute("DELETE FROM suggestions WHERE task_id = ?", (cid,))
        if child_ids:
            conn.execute("DELETE FROM tasks WHERE parent_id = ?", (task_id,))
        conn.execute("DELETE FROM suggestions WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return task


# ---------- suggestions ----------


def add_suggestion(task_id: int, text: str) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO suggestions (task_id, text, created_at) VALUES (?, ?, ?)",
            (task_id, text, now),
        )
        new_id = cur.lastrowid
    with _connect() as conn:
        row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (new_id,)).fetchone()
        return dict(row)


def list_suggestions(task_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM suggestions WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_suggestion(suggestion_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM suggestions WHERE id = ?", (suggestion_id,))
        return cur.rowcount > 0


# ---------- memories ----------


def add_memory(fact: str) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO memories (fact, created_at) VALUES (?, ?)", (fact, now)
        )
        new_id = cur.lastrowid
    with _connect() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (new_id,)).fetchone()
        return dict(row)


def list_memories(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_memory(memory_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


# ---------- proactive insights (deterministic, no AI) ----------


def get_insights() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending' AND parent_id IS NULL"
        ).fetchall()
        tasks = [_row_to_dict(r) for r in rows]

    today = date.today()
    tomorrow = today + timedelta(days=1)
    insights = []

    for t in tasks:
        if t["due_date"] == today.isoformat():
            insights.append({"type": "deadline", "task_id": t["id"], "message": f"“{t['title']}” is due today"})
        elif t["due_date"] == tomorrow.isoformat():
            insights.append({"type": "deadline", "task_id": t["id"], "message": f"“{t['title']}” is due tomorrow"})

    by_date: dict[str, list[dict]] = {}
    for t in tasks:
        if t["due_date"]:
            by_date.setdefault(t["due_date"], []).append(t)
    for d, group in by_date.items():
        if len(group) > 3:
            insights.append({"type": "overload", "message": f"{len(group)} tasks due {d} — consider spreading some out"})

    for t in tasks:
        if t.get("reschedule_count", 0) >= 3:
            insights.append({"type": "postponed", "task_id": t["id"], "message": f"“{t['title']}” has been rescheduled {t['reschedule_count']} times"})

    for t in tasks:
        if (t.get("estimated_duration_minutes") or 0) > 240 and not list_subtasks(t["id"]):
            insights.append({"type": "large_task", "task_id": t["id"], "message": f"“{t['title']}” looks big — want me to break it into phases?"})

    return insights
