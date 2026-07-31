import json
import os

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Noted")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic()

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """You turn a casual spoken or typed note into a structured task.

Extract:
- action: a short verb phrase for what needs to be done (e.g. "Text", "Call", "Email", "Buy", "Finish"). Use the clearest single verb/short phrase implied by the note.
- person: who it involves, if anyone is named or clearly implied. null if not applicable.
- topic: what it's about, in a few words.
- time: when, normalized from casual phrasing (e.g. "later today" -> "Later today", "tmrw morning" -> "Tomorrow morning"). Keep the user's specificity — don't invent a timestamp they didn't give. null if no time was mentioned.
- priority: "Low", "Medium", or "High", inferred from urgency language ("ASAP", "urgent", "whenever"). Default "Medium" if unclear.

If the note is gibberish, too vague, or does not describe an actionable task, set task_found to false and leave the other fields null (priority still defaults to "Medium").

Respond only with the extraction — no commentary."""

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "task_found": {"type": "boolean"},
        "action": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "person": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "topic": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "time": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
    },
    "required": ["task_found", "action", "person", "topic", "time", "priority"],
    "additionalProperties": False,
}


class ExtractRequest(BaseModel):
    note: str


class ExtractedTask(BaseModel):
    task_found: bool
    action: str | None
    person: str | None
    topic: str | None
    time: str | None
    priority: str


@app.post("/extract-task", response_model=ExtractedTask)
def extract_task(req: ExtractRequest):
    note = req.note.strip()
    if not note:
        raise HTTPException(status_code=400, detail="Please type or speak a note first.")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            thinking={"type": "disabled"},
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": TASK_SCHEMA},
            },
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": note}],
        )
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Claude API error: {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="Could not reach the Claude API.")

    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    return ExtractedTask(**data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8010, reload=True)
