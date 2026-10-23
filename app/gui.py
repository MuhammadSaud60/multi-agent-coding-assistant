"""VS Code-like web GUI for the multi-agent coding assistant.

Run (from the project root):

    python -m app.gui

then open http://127.0.0.1:8765

The FastAPI app serves a single static page (no build step), streams agent
progress over Server-Sent Events while the LangGraph workflow runs in a
background thread, and exposes the generated-project workspace as a file
tree plus file contents.
"""

import asyncio
import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_workspace_root
from app.graph.workflow import workflow
from app.main import build_initial_state

STATIC_DIR = Path(__file__).resolve().parent / "static"
WORKSPACE = get_workspace_root()

AGENT_NAMES = ["supervisor", "planner", "developer", "tester", "debugger"]

app = FastAPI(title="Multi-Agent Coding Assistant")


class RunRequest(BaseModel):
    request: str


def _describe(node: str, update: dict) -> str:
    """One-line human summary of a node's state update, for the event log."""
    if node == "supervisor":
        return f"Routed to {update.get('next_agent', '?')}"
    if node == "planner":
        return "Development plan created"
    if node == "developer":
        files = update.get("files_created", [])
        return (
            f"Created {len(files)} file(s): {', '.join(files)}"
            if files
            else "No files created"
        )
    if node == "tester":
        result = update.get("test_result", "")
        return result.splitlines()[0] if result else "Tests finished"
    if node == "debugger":
        return f"Fix attempted (retry {update.get('retry_count', '?')}/3)"
    return "Done"


class RunManager:
    """Owns the single active workflow run and its event fan-out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.current_agent: str | None = None
        self.agent_status = {name: "idle" for name in AGENT_NAMES}
        self.last_request = ""
        self.last_summary: dict | None = None
        self.events: list[dict] = []
        self._subscribers: list[queue.Queue] = []

    # -- event plumbing -------------------------------------------------
    def emit(self, event: dict) -> None:
        self.events.append(event)
        for subscriber in list(self._subscribers):
            subscriber.put(event)

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue()
        self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    # -- run control ----------------------------------------------------
    def start(self, user_request: str) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
        thread = threading.Thread(
            target=self._run, args=(user_request,), daemon=True
        )
        thread.start()
        return True

    def _run(self, user_request: str) -> None:
        self.last_request = user_request
        self.last_summary = None
        self.agent_status = {name: "idle" for name in AGENT_NAMES}
        self.current_agent = None
        self.emit({"type": "run_started", "request": user_request})

        summary = {
            "plan": "",
            "files_created": [],
            "entrypoint": "",
            "test_result": "",
            "final_response": "",
        }

        try:
            for chunk in workflow.stream(
                build_initial_state(user_request), stream_mode="updates"
            ):
                for node, update in chunk.items():
                    if node not in self.agent_status:
                        continue
                    self.current_agent = node
                    self.agent_status[node] = "active"
                    self.emit(
                        {
                            "type": "agent_started",
                            "agent": node,
                        }
                    )
                    for key in summary:
                        if update.get(key):
                            summary[key] = update[key]
                    self.agent_status[node] = "done"
                    self.emit(
                        {
                            "type": "agent",
                            "agent": node,
                            "message": _describe(node, update),
                            "test_result": update.get("test_result", ""),
                            "plan": update.get("plan", "")
                            if node == "planner"
                            else "",
                            "files_created": update.get("files_created", [])
                            if node == "developer"
                            else [],
                        }
                    )
            self.last_summary = summary
            self.emit({"type": "run_finished", "summary": summary})
        except Exception as exc:  # surface, never swallow
            self.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            self.current_agent = None
            self.running = False
            self.emit({"type": "status", "status": self.status()})

    def status(self) -> dict:
        return {
            "running": self.running,
            "current_agent": self.current_agent,
            "agents": dict(self.agent_status),
            "workspace": str(WORKSPACE),
            "last_request": self.last_request,
            "last_summary": self.last_summary,
        }


manager = RunManager()


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------

@app.get("/api/files")
def api_files() -> dict:
    """Nested file tree of the generated-project workspace."""

    def build(directory: Path) -> list[dict]:
        entries = []
        for path in sorted(
            directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
        ):
            if path.name == "__pycache__":
                continue
            rel = str(path.relative_to(WORKSPACE))
            if path.is_dir():
                entries.append(
                    {
                        "name": path.name,
                        "path": rel,
                        "type": "dir",
                        "children": build(path),
                    }
                )
            else:
                entries.append({"name": path.name, "path": rel, "type": "file"})
        return entries

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return {"root": str(WORKSPACE), "tree": build(WORKSPACE)}


@app.get("/api/file")
def api_file(path: str = Query(...)) -> dict:
    target = (WORKSPACE / path).resolve()
    try:
        target.relative_to(WORKSPACE)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside workspace")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "path": path,
        "content": target.read_text(encoding="utf-8", errors="replace"),
    }


@app.post("/api/run")
def api_run(body: RunRequest) -> dict:
    request = body.request.strip()
    if not request:
        raise HTTPException(status_code=400, detail="Request is empty")
    if not manager.start(request):
        raise HTTPException(status_code=409, detail="A run is already in progress")
    return {"status": "started"}


@app.get("/api/status")
def api_status() -> dict:
    return manager.status()


@app.get("/api/events")
async def api_events() -> StreamingResponse:
    subscriber = manager.subscribe()

    async def stream():
        try:
            # Replay the current run's log so late joiners catch up.
            for event in list(manager.events):
                yield f"data: {json.dumps(event)}\n\n"
            while True:
                try:
                    event = subscriber.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)
        finally:
            manager.unsubscribe(subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ----------------------------------------------------------------------
# Static frontend
# ----------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    print("Multi-Agent Coding Assistant GUI: http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
