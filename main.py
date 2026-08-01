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


class TaskCreateRequest(BaseModel):
    title: str
    description: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    priority: str | None = "Medium"
    category: str | None = None
    tags: list[str] | None = None
    estimated_duration_minutes: int | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    priority: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    estimated_duration_minutes: int | None = None
    status: str | None = None


@app.post("/command")
def command(req: CommandRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Please type or speak something first.")
    return assistant.run_command(text)


@app.get("/tasks")
def list_tasks():
    return db.list_tasks()


@app.post("/tasks")
def create_task(payload: TaskCreateRequest):
    return db.create_task(**payload.model_dump(exclude_none=True))


@app.patch("/tasks/{task_id}")
def patch_task(task_id: int, patch: TaskPatch):
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    data = patch.model_dump(exclude_unset=True)
    status = data.pop("status", None)
    if data:
        db.update_task(task_id, **data)
    if status:
        db.set_status(task_id, status)
    return db.get_task(task_id)


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    task = db.delete_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8010, reload=True)
