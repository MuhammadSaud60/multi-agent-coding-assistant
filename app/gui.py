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

from app.agents.answerer import answer_question
from app.agents.tester import pick_workspace_entrypoint, scan_workspace_files
from app.config import get_workspace_root
from app.direct_actions import handle_delete_request
from app.graph.workflow import workflow
from app.intent import is_question, is_test_only_request
from app.main import build_initial_state
from app.tools.workspace_tools import clear_workspace

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
        # The previous run's result is context for answering a
        # question ("how to run it?" right after a build), so it is
        # captured before the per-run state is reset below.
        previous_test_result = ""
        if self.last_summary:
            previous_test_result = self.last_summary.get("test_result", "")

        self.last_request = user_request
        self.last_summary = None
        self.agent_status = {name: "idle" for name in AGENT_NAMES}
        self.current_agent = None
        self.emit({"type": "run_started", "request": user_request})

        # A question is not a command: answer it from the current
        # workspace state and present the answer as the run's
        # result. Checked FIRST, before the delete and run/test
        # paths. No agents run, nothing is generated, and the
        # workspace is NOT cleared.
        if is_question(user_request):
            answer = answer_question(user_request, previous_test_result)
            self.agent_status["supervisor"] = "done"
            self.emit(
                {
                    "type": "log",
                    "message": (
                        "Question: answered directly "
                        "(no agents run, workspace untouched)."
                    ),
                }
            )
            self.emit(
                {
                    "type": "agent",
                    "agent": "supervisor",
                    "message": answer,
                    "test_result": "",
                    "plan": "",
                    "files_created": [],
                }
            )
            files = scan_workspace_files()
            summary = {
                "plan": "",
                "files_created": [],
                "entrypoint": pick_workspace_entrypoint(files) or "",
                "test_result": answer,
                "final_response": answer,
            }
            self.last_summary = summary
            self.emit({"type": "run_finished", "summary": summary})
            self.current_agent = None
            self.running = False
            self.emit({"type": "status", "status": self.status()})
            return

        # A pure delete request is an operation on the workspace,
        # not a build: perform it directly and present the outcome
        # as the run's result. No agents run, nothing is generated,
        # and the workspace is NOT cleared for a new run first.
        delete_outcome = handle_delete_request(user_request)

        if delete_outcome is not None:
            self.agent_status["supervisor"] = "done"
            self.emit(
                {
                    "type": "log",
                    "message": (
                        "Delete request: handled directly "
                        "(no agents run, nothing generated)."
                    ),
                }
            )
            self.emit(
                {
                    "type": "agent",
                    "agent": "supervisor",
                    "message": delete_outcome,
                    "test_result": "",
                    "plan": "",
                    "files_created": [],
                }
            )
            summary = {
                "plan": "",
                "files_created": [],
                "entrypoint": "",
                "test_result": delete_outcome,
                "final_response": delete_outcome,
            }
            self.last_summary = summary
            self.emit({"type": "run_finished", "summary": summary})
            self.current_agent = None
            self.running = False
            self.emit({"type": "status", "status": self.status()})
            return

        if is_test_only_request(user_request):
            # Run/test-only: the existing workspace IS the subject of
            # the request. It is kept exactly as it is.
            self.emit(
                {
                    "type": "log",
                    "message": (
                        "Run/test-only request: testing the existing "
                        "workspace (nothing cleared)."
                    ),
                }
            )
        else:
            # Start every build run from an empty workspace so files
            # from previous runs cannot pollute this one. The previous
            # project is not deleted: it is moved to a backup folder.
            cleared = clear_workspace()
            if cleared.backup_dir is not None:
                message = (
                    "Workspace cleared for the new run "
                    f"({cleared.moved} item(s) moved to backup: "
                    f"{cleared.backup_dir})."
                )
            else:
                message = (
                    "Workspace cleared for the new run "
                    "(workspace was already empty)."
                )
            self.emit({"type": "log", "message": message})

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
            # A web project can be SEEN, not just validated: point
            # at its live preview (the workspace is served under
            # /preview/, see the mount below).
            entrypoint = summary.get("entrypoint", "")
            if entrypoint.lower().endswith((".html", ".htm")):
                preview_url = f"http://127.0.0.1:8765/preview/{entrypoint}"
                summary["preview_url"] = preview_url
                self.emit(
                    {
                        "type": "preview",
                        "url": preview_url,
                        "entrypoint": entrypoint,
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


@app.get("/api/preview-info")
def api_preview_info() -> dict:
    """Whether the workspace currently holds a previewable web page.

    Drives the persistent "Open preview" link in the Explorer: it is
    offered exactly when the workspace project has an HTML
    entrypoint, which is what /preview/ can serve.
    """
    files = scan_workspace_files()
    entrypoint = pick_workspace_entrypoint(files)
    if entrypoint and entrypoint.lower().endswith((".html", ".htm")):
        return {
            "available": True,
            "entrypoint": entrypoint,
            "url": f"/preview/{entrypoint}",
        }
    return {"available": False, "entrypoint": "", "url": ""}


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

# The generated workspace, served read-only so a web project can be
# previewed in the browser at /preview/<entrypoint>. StaticFiles is
# rooted at the workspace and normalises every path it serves, so a
# request can never escape the workspace and reach the application's
# own source (/../ attempts 404). The directory must exist before
# the mount is created.
WORKSPACE.mkdir(parents=True, exist_ok=True)
app.mount(
    "/preview",
    StaticFiles(directory=WORKSPACE, html=True),
    name="preview",
)


def main() -> None:
    import uvicorn

    print("Multi-Agent Coding Assistant GUI: http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
