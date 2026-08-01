import json
import sqlite3
from datetime import datetime, timezone
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
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date);
"""

EDITABLE_FIELDS = {
    "title",
    "description",
    "due_date",
    "due_time",
    "priority",
    "category",
    "tags",
    "estimated_duration_minutes",
}


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")


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
                 tags, estimated_duration_minutes, status, created_at, updated_at)
            VALUES (:title, :description, :due_date, :due_time, :priority, :category,
                    :tags, :estimated_duration_minutes, 'pending', :created_at, :updated_at)
            """,
            {**values, "created_at": now, "updated_at": now},
        )
        new_id = cur.lastrowid

    return get_task(new_id)


def get_task(task_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_tasks(status: str | None = None) -> list[dict]:
    query = "SELECT * FROM tasks"
    params: tuple = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    query += """
        ORDER BY
            CASE status WHEN 'pending' THEN 0 ELSE 1 END,
            CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
            due_date ASC,
            CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END
    """
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def update_task(task_id: int, **fields) -> dict | None:
    updates = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS and v is not None}
    if not updates:
        return get_task(task_id)

    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"])

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


def delete_task(task_id: int) -> dict | None:
    task = get_task(task_id)
    if task is None:
        return None
    with _connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return task
