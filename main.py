from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import assistant
import db

app = FastAPI(title="Noted")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()


class CommandRequest(BaseModel):
    text: str
    lang: str = "en"


class TaskCreateRequest(BaseModel):
    title: str
    description: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    priority: str | None = "Medium"
    category: str | None = None
    tags: list[str] | None = None
    estimated_duration_minutes: int | None = None
    recurrence: str | None = None
    parent_id: int | None = None
    lang: str = "en"


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    priority: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    estimated_duration_minutes: int | None = None
    recurrence: str | None = None
    status: str | None = None


@app.post("/command")
def command(req: CommandRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Please type or speak something first.")
    return assistant.run_command(text, lang=req.lang)


@app.get("/tasks")
def list_tasks():
    return db.list_tasks()


@app.post("/tasks")
def create_task(payload: TaskCreateRequest):
    data = payload.model_dump(exclude_none=True)
    lang = data.pop("lang", "en")
    task = db.create_task(**data)
    for suggestion in assistant.generate_suggestions(task, lang=lang):
        db.add_suggestion(task["id"], suggestion)
    return task


@app.patch("/tasks/{task_id}")
def patch_task(task_id: int, patch: TaskPatch):
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    data = patch.model_dump(exclude_unset=True)
    status = data.pop("status", None)
    if data:
        db.update_task(task_id, **data)
    if status == "completed":
        db.complete_task_with_recurrence(task_id)
    elif status:
        db.set_status(task_id, status)
    return db.get_task(task_id)


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    task = db.delete_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.get("/insights")
def insights():
    return db.get_insights()


@app.post("/briefing")
def briefing(lang: str = "en"):
    pending = db.list_tasks(status="pending")
    today_str = date.today().isoformat()
    due_today = [t for t in pending if t.get("due_date") == today_str]
    overdue = [t for t in pending if t.get("due_date") and t["due_date"] < today_str]
    message = assistant.generate_briefing(due_today, overdue, db.get_insights(), lang=lang)
    return {"message": message}


@app.delete("/suggestions/{suggestion_id}")
def delete_suggestion(suggestion_id: int):
    if not db.delete_suggestion(suggestion_id):
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    return {"ok": True}


@app.get("/memories")
def list_memories():
    return db.list_memories()


@app.delete("/memories/{memory_id}")
def delete_memory(memory_id: int):
    if not db.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8010, reload=True)
