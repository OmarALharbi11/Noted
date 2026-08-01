import re
from datetime import datetime

import anthropic

import db

client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

TOOLS = [
    {
        "name": "create_task",
        "description": (
            "Create a new task. Use for phrases like 'remind me to...', 'add a task...', "
            "'I need to...'. Resolve relative dates/times ('tomorrow', 'next Tuesday', 'in an "
            "hour') into absolute due_date/due_time using today's date given in the system "
            "prompt. If no date/time was mentioned, omit due_date/due_time — don't invent one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short, clear task title."},
                "description": {"type": "string"},
                "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD. Omit if not given."},
                "due_time": {"type": "string", "description": "24-hour ISO time HH:MM. Omit if not given."},
                "priority": {"type": "string", "enum": ["Low", "Medium", "High"], "description": "Default Medium if unclear."},
                "category": {"type": "string", "description": "Short topic label, e.g. 'Work', 'Health'."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "estimated_duration_minutes": {"type": "integer"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": (
            "Modify an existing task — rename, reschedule, change priority/category/tags/"
            "description/duration. Only include fields that should change; omitted fields stay "
            "as-is. Resolve which task via task_id using the current task list in the system "
            "prompt (e.g. 'the gym task', 'it', 'move it to Friday'). If more than one task "
            "could plausibly match, call ask_clarification instead of guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "due_date": {"type": "string"},
                "due_time": {"type": "string"},
                "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
                "category": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "estimated_duration_minutes": {"type": "integer"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "delete_task",
        "description": (
            "Permanently delete a task. Not for finishing a task (use complete_task). "
            "If multiple pending tasks could match, call ask_clarification."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task as done. Use for 'I finished...', 'done with...', 'mark X complete'.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "ask_clarification",
        "description": (
            "Use INSTEAD of any other tool when: the command could match more than one existing "
            "task; required info is missing and can't be reasonably inferred; or the input isn't "
            "an actionable task command at all (e.g. gibberish, 'never mind'). Call ONLY this "
            "tool in that turn — no others."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
    },
]


def build_system_prompt(pending_tasks: list[dict]) -> str:
    today_str = datetime.now().strftime("%A, %Y-%m-%d")
    task_lines = "\n".join(
        f"- id={t['id']} | {t['title']} | category={t['category'] or '—'} | "
        f"due={t['due_date'] or '—'} {t['due_time'] or ''} | priority={t['priority']}"
        for t in pending_tasks
    ) or "(no pending tasks)"
    return f"""You are the command interpreter for Noted, a personal task manager.
Today is {today_str}. Resolve all relative dates/times against this.

Current pending tasks:
{task_lines}

Turn the user's command into exactly one or more tool calls. Bulk commands
("move all study tasks to tomorrow") should produce one update_task call per
matching task. Never respond with plain text — always call a tool."""


def _due_suffix(task: dict) -> str:
    if task.get("due_date"):
        time_part = f" {task['due_time']}" if task.get("due_time") else ""
        return f" — {task['due_date']}{time_part}"
    return ""


def _warn_suffix(warnings: list[str]) -> str:
    return f" ({'; '.join(warnings)})" if warnings else ""


def _clean_date_time_fields(input_data: dict) -> tuple[dict, list[str]]:
    warnings = []
    if input_data.get("due_date") is not None and not DATE_RE.match(str(input_data["due_date"])):
        warnings.append("couldn't resolve the date")
        del input_data["due_date"]
    if input_data.get("due_time") is not None and not TIME_RE.match(str(input_data["due_time"])):
        warnings.append("couldn't resolve the time")
        del input_data["due_time"]
    return input_data, warnings


def _execute_tool(name: str, raw_input: dict) -> dict | None:
    input_data = dict(raw_input)
    input_data, warnings = _clean_date_time_fields(input_data)

    if name == "create_task":
        task = db.create_task(**input_data)
        summary = f"Created: {task['title']}{_due_suffix(task)}{_warn_suffix(warnings)}"
        return {"type": "created", "task": task, "summary": summary}

    if name == "update_task":
        task_id = input_data.pop("task_id", None)
        existing = db.get_task(task_id) if task_id is not None else None
        if existing is None:
            return {"type": "skipped", "task": None, "summary": "Couldn't find that task — it may have already been changed."}
        task = db.update_task(task_id, **input_data)
        summary = f"Updated: {task['title']}{_due_suffix(task)}{_warn_suffix(warnings)}"
        return {"type": "updated", "task": task, "summary": summary}

    if name == "delete_task":
        task_id = input_data.get("task_id")
        task = db.delete_task(task_id) if task_id is not None else None
        if task is None:
            return {"type": "skipped", "task": None, "summary": "Couldn't find that task to delete."}
        return {"type": "deleted", "task": task, "summary": f"Deleted: {task['title']}"}

    if name == "complete_task":
        task_id = input_data.get("task_id")
        existing = db.get_task(task_id) if task_id is not None else None
        if existing is None:
            return {"type": "skipped", "task": None, "summary": "Couldn't find that task to complete."}
        task = db.set_status(task_id, "completed")
        return {"type": "completed", "task": task, "summary": f"Completed: {task['title']}"}

    return None


def _combine_messages(actions: list[dict]) -> str:
    real = [a for a in actions if a["type"] != "skipped"]
    if len(real) == 1:
        return real[0]["summary"]
    if len(real) > 1:
        counts: dict[str, int] = {}
        for a in real:
            counts[a["type"]] = counts.get(a["type"], 0) + 1
        parts = [f"{count} task{'s' if count != 1 else ''} {kind}" for kind, count in counts.items()]
        return ", ".join(parts).capitalize()
    return "; ".join(a["summary"] for a in actions)


def run_command(text: str) -> dict:
    pending = db.list_tasks(status="pending")
    system = build_system_prompt(pending)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            tool_choice={"type": "any"},
            tools=TOOLS,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.APIStatusError as e:
        return {"status": "error", "message": f"Claude API error: {e.message}"}
    except anthropic.APIConnectionError:
        return {"status": "error", "message": "Could not reach the Claude API."}

    tool_calls = [b for b in response.content if b.type == "tool_use"]
    if not tool_calls:
        return {"status": "error", "message": "Didn't understand that — try rephrasing."}

    clarification = next((b for b in tool_calls if b.name == "ask_clarification"), None)
    if clarification is not None:
        return {
            "status": "clarification_needed",
            "question": clarification.input.get("question", "Could you clarify?"),
            "options": clarification.input.get("options") or [],
        }

    actions = [_execute_tool(call.name, call.input) for call in tool_calls]
    actions = [a for a in actions if a is not None]

    real_actions = [a for a in actions if a["type"] != "skipped"]
    if not real_actions:
        message = actions[0]["summary"] if actions else "Nothing was changed."
        return {"status": "error", "message": message}

    return {
        "status": "ok",
        "message": _combine_messages(actions),
        "actions": [{"type": a["type"], "task": a["task"]} for a in real_actions],
        "tasks": db.list_tasks(),
    }
