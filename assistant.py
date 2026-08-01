import json
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
                "recurrence": {"type": "string", "enum": ["daily", "weekdays", "weekly", "monthly"], "description": "Only if the user explicitly wants this to repeat."},
                "parent_task_id": {"type": "integer", "description": "Set only if this is explicitly a subtask of an existing task. Omit for normal top-level tasks."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": (
            "Modify an existing task — rename, reschedule, change priority/category/tags/"
            "description/duration/recurrence. Only include fields that should change; omitted "
            "fields stay as-is. Resolve which task via task_id using the current task list in "
            "the system prompt (e.g. 'the gym task', 'it', 'move it to Friday'). If more than "
            "one task could plausibly match, call ask_clarification instead of guessing."
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
                "recurrence": {"type": "string", "enum": ["daily", "weekdays", "weekly", "monthly"]},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "create_subtasks",
        "description": (
            "Add one or more subtasks under an existing task. Use for explicit requests to "
            "break a task into subtasks or phases (e.g. 'split the report into subtasks', "
            "'break this project into phases'). If the user doesn't list subtask titles "
            "themselves, propose a reasonable, concrete breakdown yourself (e.g. research, "
            "design, build, test, submit) — you're expected to generate sensible phase names, "
            "not ask the user to supply them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parent_task_id": {"type": "integer"},
                "subtasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD, optional."},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["parent_task_id", "subtasks"],
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
        "name": "remember",
        "description": (
            "Record a lasting preference, routine, or schedule pattern the user reveals — "
            "worth remembering for future commands (e.g. preferred working hours, recurring "
            "routines, general habits). Do not use this for one-off task details — those "
            "belong in the task itself via create_task/update_task. Can be called alongside "
            "another tool, or alone if the user shares context with no specific task action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "The fact to remember, phrased plainly."}},
            "required": ["fact"],
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

SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}},
    "required": ["suggestions"],
    "additionalProperties": False,
}

SUGGESTION_SYSTEM = """You are an experienced executive assistant giving realistic, practical advice for a task someone just added to their list.

Suggest 2-4 concrete, task-specific next steps or considerations that would genuinely help them complete this exact task — missing prerequisite steps, deadline-driven prep, practical logistics. Consider the task's due date and priority if given.

Rules:
- Be specific to this task, not generic.
- Never suggest generic advice like "stay motivated," "manage your time well," "work hard," or similar.
- If nothing concrete applies, return an empty list — don't invent filler.
- Each suggestion is a short, actionable phrase, under 12 words."""

BRIEFING_SYSTEM = """You are a concise, warm executive assistant giving a short daily briefing.

Given the user's tasks due today, anything overdue, and current insights, write a brief natural-language summary (3-5 sentences) that:
- Highlights what's due today, calling out high-priority items.
- Flags anything overdue.
- Gives one practical recommendation on what to tackle first, if it's not obvious.
- Skips any section that's empty — don't say "nothing overdue," just omit it.

Keep it conversational and brief — this is meant to be read in a few seconds, not a report."""


def build_system_prompt(pending_tasks: list[dict], memories: list[str] | None = None) -> str:
    today_str = datetime.now().strftime("%A, %Y-%m-%d")

    def task_line(t: dict, indent: str = "") -> str:
        return (
            f"{indent}- id={t['id']} | {t['title']} | category={t['category'] or '—'} | "
            f"due={t['due_date'] or '—'} {t['due_time'] or ''} | priority={t['priority']}"
        )

    lines = []
    for t in pending_tasks:
        lines.append(task_line(t))
        for sub in t.get("subtasks", []):
            if sub["status"] == "pending":
                lines.append(task_line(sub, indent="    ") + " (subtask)")
    task_lines = "\n".join(lines) or "(no pending tasks)"

    memory_section = ""
    if memories:
        memory_section = "\nWhat you remember about this user:\n" + "\n".join(f"- {m}" for m in memories) + "\n"

    return f"""You are the command interpreter for Noted, a personal task manager.
Today is {today_str}. Resolve all relative dates/times against this.
{memory_section}
Current pending tasks (subtasks are indented under their parent):
{task_lines}

Turn the user's command into exactly one or more tool calls. Bulk commands
("move all study tasks to tomorrow") should produce one update_task call per
matching task. If the user shares a lasting preference or routine, call
remember. Never respond with plain text — always call at least one tool."""


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


def generate_suggestions(task: dict) -> list[str]:
    details = f"Task: {task['title']}"
    if task.get("description"):
        details += f"\nDetails: {task['description']}"
    if task.get("due_date"):
        details += f"\nDue: {task['due_date']}" + (f" {task['due_time']}" if task.get("due_time") else "")
    if task.get("priority"):
        details += f"\nPriority: {task['priority']}"
    if task.get("category"):
        details += f"\nCategory: {task['category']}"

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=SUGGESTION_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SUGGESTION_SCHEMA}},
            messages=[{"role": "user", "content": details}],
        )
    except Exception:
        # Suggestions are best-effort — never let a failure here block task creation
        # (e.g. missing/invalid API credentials, rate limits, network issues).
        return []

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        return []
    try:
        return json.loads(text).get("suggestions") or []
    except json.JSONDecodeError:
        return []


def generate_briefing(due_today: list[dict], overdue: list[dict], insights: list[dict]) -> str:
    lines = []
    if due_today:
        lines.append("Due today:")
        for t in due_today:
            time_part = f" at {t['due_time']}" if t.get("due_time") else ""
            lines.append(f"- {t['title']} (priority: {t['priority']}){time_part}")
    if overdue:
        lines.append("Overdue:")
        for t in overdue:
            lines.append(f"- {t['title']} (was due {t['due_date']})")
    if insights:
        lines.append("Other notes:")
        for i in insights:
            lines.append(f"- {i['message']}")

    if not lines:
        return "Nothing due today, and nothing overdue — you're clear."

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=BRIEFING_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
    except Exception:
        return "Couldn't generate a briefing right now."

    text = next((b.text for b in response.content if b.type == "text"), None)
    return text or "Couldn't generate a briefing right now."


def _execute_tool(name: str, raw_input: dict) -> list[dict]:
    input_data = dict(raw_input)

    if name == "remember":
        fact = (input_data.get("fact") or "").strip()
        if not fact:
            return []
        db.add_memory(fact)
        return [{"type": "remembered", "task": None, "summary": f"Noted: {fact}"}]

    if name == "create_subtasks":
        parent_id = input_data.get("parent_task_id")
        parent = db.get_task(parent_id) if parent_id is not None else None
        if parent is None:
            return [{"type": "skipped", "task": None, "summary": "Couldn't find that task to add subtasks to."}]
        created = db.create_subtasks(parent_id, input_data.get("subtasks") or [])
        count = len(created)
        summary = f"Added {count} subtask{'s' if count != 1 else ''} to {parent['title']}"
        return [{"type": "subtasks_created", "task": parent, "summary": summary}]

    input_data, warnings = _clean_date_time_fields(input_data)

    if name == "create_task":
        if "parent_task_id" in input_data:
            input_data["parent_id"] = input_data.pop("parent_task_id")
        task = db.create_task(**input_data)
        for suggestion in generate_suggestions(task):
            db.add_suggestion(task["id"], suggestion)
        summary = f"Created: {task['title']}{_due_suffix(task)}{_warn_suffix(warnings)}"
        return [{"type": "created", "task": task, "summary": summary}]

    if name == "update_task":
        task_id = input_data.pop("task_id", None)
        existing = db.get_task(task_id) if task_id is not None else None
        if existing is None:
            return [{"type": "skipped", "task": None, "summary": "Couldn't find that task — it may have already been changed."}]
        task = db.update_task(task_id, **input_data)
        summary = f"Updated: {task['title']}{_due_suffix(task)}{_warn_suffix(warnings)}"
        return [{"type": "updated", "task": task, "summary": summary}]

    if name == "delete_task":
        task_id = input_data.get("task_id")
        task = db.delete_task(task_id) if task_id is not None else None
        if task is None:
            return [{"type": "skipped", "task": None, "summary": "Couldn't find that task to delete."}]
        return [{"type": "deleted", "task": task, "summary": f"Deleted: {task['title']}"}]

    if name == "complete_task":
        task_id = input_data.get("task_id")
        existing = db.get_task(task_id) if task_id is not None else None
        if existing is None:
            return [{"type": "skipped", "task": None, "summary": "Couldn't find that task to complete."}]
        completed, recurred = db.complete_task_with_recurrence(task_id)
        results = [{"type": "completed", "task": completed, "summary": f"Completed: {completed['title']}"}]
        if recurred:
            results.append({"type": "created", "task": recurred, "summary": f"Next occurrence: {recurred['title']}{_due_suffix(recurred)}"})
        return results

    return []


def _combine_messages(actions: list[dict]) -> str:
    real = [a for a in actions if a["type"] != "skipped"]
    if len(real) == 1:
        return real[0]["summary"]
    if len(real) > 1:
        task_like = [a for a in real if a["type"] != "remembered"]
        remembered_count = len(real) - len(task_like)
        parts = []
        if task_like:
            counts: dict[str, int] = {}
            for a in task_like:
                counts[a["type"]] = counts.get(a["type"], 0) + 1
            parts.append(", ".join(f"{count} task{'s' if count != 1 else ''} {kind}" for kind, count in counts.items()))
        if remembered_count:
            parts.append("remembered that for later")
        return ", ".join(parts).capitalize() if parts else "Done."
    return "; ".join(a["summary"] for a in actions)


def run_command(text: str) -> dict:
    pending = db.list_tasks(status="pending")
    memories = [m["fact"] for m in db.list_memories()]
    system = build_system_prompt(pending, memories)

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
    except Exception as e:
        return {"status": "error", "message": f"Couldn't process that command: {e}"}

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

    actions: list[dict] = []
    for call in tool_calls:
        actions.extend(_execute_tool(call.name, call.input))

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
