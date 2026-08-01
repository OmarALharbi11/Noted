# Noted

A voice- and text-controlled personal task manager. Instead of forms and menus, you talk to it naturally — "remind me to call Ahmed tomorrow at 3pm", "move the presentation to Friday", "delete the gym task" — and it creates, edits, reschedules, or deletes the right task instantly.

**Live demo:** [omaralharbi11.github.io/Noted](https://omaralharbi11.github.io/Noted/) (frontend on GitHub Pages, backend on Render's free tier — the backend spins down after 15 minutes idle, so the first request after a while can take ~30-50s to wake up)

## What it does

- **Natural-language command routing** — typed or spoken input goes through one pipeline (Claude tool-calling) that decides whether to create, edit, reschedule, complete, or delete a task, and resolves references like "the gym task" against your actual current task list. If a command is ambiguous (two tasks could match) or unclear, it asks a clarifying question instead of guessing.
- **Persistent storage** — a real SQLite-backed task list (title, due date/time, priority, category, tags, estimated duration), not a session-only log.
- **Subtasks & task breakdown** — "break the final year project into phases" creates a sensible set of subtasks automatically.
- **Recurring tasks** — daily/weekdays/weekly/monthly; completing one automatically creates the next occurrence.
- **AI-generated suggestions** — concrete, task-specific next steps (not generic advice) generated automatically when a task is created.
- **Proactive insights** — approaching deadlines, overloaded days, and repeatedly-postponed tasks are surfaced automatically, computed deterministically with no extra AI cost.
- **On-demand daily briefing** and a **memory system** that remembers stated preferences/routines across commands.
- **Voice input** — tap the mic, talk, and the command fires the moment you stop — no separate "send" step for voice.

## Architecture

```
index.html  ──POST /command──▶  FastAPI (main.py)
 (voice/text)                        │
                                      ▼
                              assistant.py — builds a system prompt from
                              live task state, calls Claude (tool-calling:
                              create_task / update_task / delete_task /
                              complete_task / create_subtasks / remember /
                              ask_clarification), executes whatever the
                              model calls against db.py
                                      │
                                      ▼
                              db.py — SQLite (tasks, suggestions, memories)
```

Command routing runs on `claude-haiku-4-5` — fast and cheap for structured tool-calling — with `tool_choice: "any"` so every command resolves to an action or a clarifying question, never freeform text the backend can't act on.

## Running locally

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Set your Anthropic API key:
   ```
   set ANTHROPIC_API_KEY=your-key-here      # Windows cmd
   $env:ANTHROPIC_API_KEY="your-key-here"   # PowerShell
   ```
3. Start the backend:
   ```
   python main.py
   ```
   Runs at `http://127.0.0.1:8010`. (To point the frontend at a local server instead of the deployed one, change `API_BASE` in `index.html`.)
4. Open `index.html` in a browser. Type or speak a command.

Voice input uses the browser's built-in Web Speech API (Chrome/Edge support it best).

## Deployment

- **Backend:** Render (see `render.yaml`) — free-tier web service running FastAPI/uvicorn. Note: the free tier has no persistent disk, so the SQLite database resets on redeploy.
- **Frontend:** GitHub Pages, serving `index.html` directly from the repo root.
