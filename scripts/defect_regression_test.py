"""Regression tests for the defects fixed on 2026-10-23.

Scenarios 1-5 cover the original three defects (web projects forced
through Python, destructive generated code executed by the tester,
stale files polluting the workspace). Scenarios 6-8 cover the
follow-up crash: a wrong entrypoint in the developer payload used to
raise a ValueError that killed the whole run; it is now corrected
when unambiguous and reported gracefully when it is not. Scenario 9
covers the next crash Saud hit: a model whose message content is a
LIST of content blocks (LangChain 1.x shape) died in the supervisor
with AttributeError: 'list' object has no attribute 'strip'.

Everything runs with the FAKE model provider (no Ollama server, no API
key). Each scenario runs in a subprocess because the agents bind their
model and workspace at import time from the MACA_* environment variables.

Scenarios:
  1. web     A website payload (index.html + styles.css + script.js,
             entrypoint index.html) is VALIDATED, not executed with
             Python, and passes.
  2. blocked A payload whose script deletes files is BLOCKED by the
             tester, is never executed, is never retried by the
             debugger, and its files are still on disk afterwards.
  3. smoke   The existing hello-world smoke test still passes
             (scripts/smoke_test.py, run as a subprocess).
  4. tworuns Two CLI runs share one workspace; the second run must not
             leave the first run's files behind in the workspace.
  5. gui     A GUI RunManager run also clears the workspace first and
             emits the "Workspace cleared" log event.
  6. entryfix_web
             A web payload whose entrypoint wrongly points at the
             generated main.py (the exact crash Saud hit locally) is
             auto-corrected to index.html and validated, no exception.
  7. entryfix_missing
             A payload whose entrypoint names a file that was never
             generated, with a single main.py present, is corrected to
             main.py and the Python test passes.
  8. entryfix_empty
             A payload with zero files ends the run gracefully with
             an honest FAILED result instead of raising.
  9. listcontent
             Saud's exact "three boxes" webpage request, run against
             fake models whose responses return LIST-typed content
             (supervisor and planner messages are lists of content
             blocks, and the developer payload's index.html content
             is a list of lines). The run must complete with no
             AttributeError and produce the web files.
 10. runexisting
             A workspace pre-populated with a valid Python project,
             then the request "run the code": the tester must run the
             EXISTING project (files byte-for-byte unchanged, known
             output reported) and the developer must never run. The
             fake developer payload is a distinctive marker file, so
             any regeneration is caught.
 11. runexisting_web
             Same as 10, but the existing project is a website: the
             tester validates it instead of executing anything, and
             the files stay untouched.
 12. backup  A normal build run over a workspace that already holds a
             project: the old file must be MOVED into a timestamped
             backup folder next to the workspace (never deleted), and
             the CLI output must say where it went.
 13. deletenamed
             "delete notes.txt" in a workspace holding main.py +
             notes.txt: notes.txt is deleted directly, main.py is
             untouched, and the developer never runs (the marker
             payload would expose it). No deletion script is ever
             generated.
 14. deleteall
             "delete all files" empties the workspace by MOVING both
             files into the timestamped backup folder, not by
             hard-deleting them.
 15. deletevague
             "delete the existing file" with two files present and
             none named: nothing is deleted and the result lists
             both files and asks for a name or "all".
 16. deleteone
             "delete the existing file" with exactly one file
             present: that file is removed from the workspace via
             the backup path.
 17. deletegui
             The same named delete driven through the GUI RunManager:
             the file is gone afterwards and the run summary carries
             the outcome.
 18. deletecli
             The same named delete driven through the CLI entry
             point as a subprocess.
 19. question
             "how to run it?" with an existing website in the
             workspace: the request is a QUESTION, not a run order.
             It must be answered (the answer explains opening
             index.html in a browser), with no tester run, no files
             changed, no workspace clearing/backup, and the
             developer never running. Driven through the graph and
             through the GUI RunManager.
 20. questionbuild
             A normal build request still builds, while "how do I
             create a login page?" (a question ABOUT building) gets
             an answer and builds nothing.
 21. preview
             The GUI serves the workspace under /preview/: the
             index page and its stylesheet come back 200, a missing
             file 404s, and path-traversal attempts never return
             the application's own source. /api/preview-info points
             at the page.
 22. editfollowup
             Two GUI runs in one workspace. Run 1 builds a web page
             (index.html + style.css). Run 2, "add a footer to the
             page", with the fake developer returning ONLY an updated
             index.html, must EDIT the existing project: edit_mode
             is true, no backup/clear happens, style.css stays
             byte-identical, and index.html gains the footer. This
             is the second-instruction defect Saud hit live.
 23. freshstart
             "start over and make a new project" on an occupied
             workspace keeps the old behaviour: edit_mode is false,
             the old project is moved to the backup folder, and the
             new project is built from scratch.
 24. nocomments
             The fake developer returns files full of comments (a
             Python file with line comments AND a string containing
             '#', an HTML file with a comment, a CSS file with
             block comments). The written files must contain no
             comments, and the '#' string must survive intact.
 25. phases The GUI driven in a real browser (Playwright): the
             chat stream and terminal show only the phase lines
             Thinking / Building / Testing and a final Done, and
             never the verbose tester dump ("MODE:", "CHECKS:").

Note on scenario 2 (blocked): its request used to be the literal
"delete all files", which is now a direct workspace action (scenario
14) and never reaches the developer. The scenario now phrases the
request as a build ("create a file cleanup script") so the fake
developer's destructive payload still exercises the tester's
BLOCKED gate exactly as before.

Usage (from the project root):

    .venv/bin/python scripts/defect_regression_test.py

Exit code 0 when every scenario passes, 1 otherwise.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))

_VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PY = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

TIMEOUT = 300

WEB_PAYLOAD = {
    "files": [
        {
            "path": "index.html",
            "content": (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '<meta charset="utf-8">\n'
                "<title>Test Page</title>\n"
                '<link rel="stylesheet" href="styles.css">\n'
                "</head>\n"
                "<body>\n"
                "<h1>Hello from the regression test</h1>\n"
                '<script src="script.js"></script>\n'
                "</body>\n"
                "</html>\n"
            ),
        },
        {
            "path": "styles.css",
            "content": "body { font-family: sans-serif; }\n",
        },
        {
            "path": "script.js",
            "content": "console.log('test page loaded');\n",
        },
    ],
    "entrypoint": "index.html",
}

BLOCKED_PAYLOAD = {
    "files": [
        {
            "path": "main.py",
            "content": (
                "import os\n"
                "\n"
                "cwd = os.getcwd()\n"
                "for name in os.listdir(cwd):\n"
                "    path = os.path.join(cwd, name)\n"
                "    if os.path.isfile(path):\n"
                "        os.remove(path)\n"
                "print('all files deleted')\n"
            ),
        }
    ],
    "entrypoint": "main.py",
}

FIRST_PAYLOAD = {
    "files": [
        {"path": "first.py", "content": "print('first run')\n"},
    ],
    "entrypoint": "first.py",
}

SECOND_PAYLOAD = {
    "files": [
        {"path": "second.py", "content": "print('second run')\n"},
    ],
    "entrypoint": "second.py",
}

# Mirrors Saud's local crash exactly: the model generated a web project
# AND a main.py, then named main.py as the entrypoint.
ENTRYFIX_WEB_PAYLOAD = {
    "files": [
        {
            "path": "index.html",
            "content": (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '<meta charset="utf-8">\n'
                "<title>Entryfix Page</title>\n"
                '<link rel="stylesheet" href="styles.css">\n'
                "</head>\n"
                "<body>\n"
                "<h1>Entrypoint fix test</h1>\n"
                "</body>\n"
                "</html>\n"
            ),
        },
        {
            "path": "styles.css",
            "content": "body { color: #222; }\n",
        },
        {
            "path": "main.py",
            "content": "print('this file must never be the web entrypoint')\n",
        },
    ],
    "entrypoint": "main.py",
}

ENTRYFIX_MISSING_PAYLOAD = {
    "files": [
        {"path": "main.py", "content": "print('hello from the real main')\n"},
    ],
    "entrypoint": "app.py",
}

ENTRYFIX_EMPTY_PAYLOAD = {
    "files": [],
    "entrypoint": "main.py",
}

# Payload for the run-existing scenarios. If the developer runs at
# all for a "run the code" request, this unmistakable file appears in
# the workspace and the scenario fails.
DEVELOPER_MARKER_PAYLOAD = {
    "files": [
        {
            "path": "developer_was_here.py",
            "content": "print('the developer regenerated the project')\n",
        },
    ],
    "entrypoint": "developer_was_here.py",
}

# Payload for scenario questionbuild's build half: a distinctive
# greeting so the scenario can prove the build really ran.
QUESTIONBUILD_PAYLOAD = {
    "files": [
        {"path": "main.py", "content": "print('built by the agents')\n"},
    ],
    "entrypoint": "main.py",
}

# Payloads for scenario editfollowup: run 1 builds the page, run 2
# returns ONLY the updated index.html (with a footer added). The
# scenario swaps the payload between runs through a payload file,
# because the fake structured model is bound once per process.
EDIT_V1_PAYLOAD = {
    "files": [
        {
            "path": "index.html",
            "content": (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '<meta charset="utf-8">\n'
                "<title>Editable Page</title>\n"
                '<link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                "<h1>Editable page</h1>\n"
                "</body>\n"
                "</html>\n"
            ),
        },
        {
            "path": "style.css",
            "content": (
                "body { font-family: sans-serif; }\n"
                "h1 { color: #333; }\n"
            ),
        },
    ],
    "entrypoint": "index.html",
}

EDIT_V2_PAYLOAD = {
    "files": [
        {
            "path": "index.html",
            "content": (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '<meta charset="utf-8">\n'
                "<title>Editable Page</title>\n"
                '<link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                "<h1>Editable page</h1>\n"
                "<footer>Added by the follow-up</footer>\n"
                "</body>\n"
                "</html>\n"
            ),
        },
    ],
    "entrypoint": "index.html",
}

# Payload for scenario freshstart: the brand-new project that
# replaces the existing one.
FRESHSTART_PAYLOAD = {
    "files": [
        {"path": "main.py", "content": "print('brand new project')\n"},
    ],
    "entrypoint": "main.py",
}

# Payload for scenario nocomments: every file is full of comments,
# and the Python file also carries a '#' inside a string literal
# that must survive the deterministic comment stripper.
NOCOMMENTS_PAYLOAD = {
    "files": [
        {
            "path": "index.html",
            "content": (
                "<!DOCTYPE html>\n"
                "<!-- page comment -->\n"
                '<html lang="en">\n'
                "<head>\n"
                '<meta charset="utf-8">\n'
                "<title>No Comments</title>\n"
                '<link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                "<h1>Clean page</h1>\n"
                "</body>\n"
                "</html>\n"
            ),
        },
        {
            "path": "style.css",
            "content": (
                "/* banner comment */\n"
                "body { color: #222; /* inline comment */ }\n"
            ),
        },
        {
            "path": "main.py",
            "content": (
                "# header comment\n"
                'LABEL = "keep # this"  # trailing comment\n'
                "\n"
                "# standalone comment\n"
                "print(LABEL)\n"
            ),
        },
    ],
    "entrypoint": "index.html",
}

# The pre-existing Python project for scenario runexisting.
EXISTING_MAIN_PY = (
    '"""Pre-existing project: must survive a run-only request."""\n'
    "\n"
    "from helper import MESSAGE\n"
    "\n"
    "print(MESSAGE)\n"
    'print("existing project ran OK")\n'
)

EXISTING_HELPER_PY = 'MESSAGE = "hello from the existing project"\n'

# The pre-existing website for scenario runexisting_web.
EXISTING_INDEX_HTML = (
    "<!DOCTYPE html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    "<title>Existing Site</title>\n"
    '<link rel="stylesheet" href="styles.css">\n'
    "</head>\n"
    "<body>\n"
    "<h1>Existing website</h1>\n"
    '<script src="script.js"></script>\n'
    "</body>\n"
    "</html>\n"
)


# Saud's "three boxes" request. The index.html content is deliberately
# a LIST of lines, the shape small models sometimes return for a
# structured-output string field; the run only succeeds if the
# developer coerces it back to text.
LISTCONTENT_REQUEST = (
    "create a webpage show three boxes of different colors apply "
    "some good looking animation to the boxes using js"
)

LISTCONTENT_PLAN = (
    "Plan: 1) index.html with three coloured boxes. "
    "2) styles.css for the box colours. "
    "3) script.js that animates the boxes."
)

LISTCONTENT_PAYLOAD = {
    "files": [
        {
            "path": "index.html",
            "content": [
                "<!DOCTYPE html>",
                '<html lang="en">',
                "<head>",
                '<meta charset="utf-8">',
                "<title>Three Boxes</title>",
                '<link rel="stylesheet" href="styles.css">',
                "</head>",
                "<body>",
                '<div class="box red"></div>',
                '<div class="box green"></div>',
                '<div class="box blue"></div>',
                '<script src="script.js"></script>',
                "</body>",
                "</html>",
            ],
        },
        {
            "path": "styles.css",
            "content": (
                ".box { width: 120px; height: 120px; margin: 16px; "
                "display: inline-block; }\n"
                ".red { background: #e74c3c; }\n"
                ".green { background: #2ecc71; }\n"
                ".blue { background: #3498db; }\n"
            ),
        },
        {
            "path": "script.js",
            "content": (
                "document.querySelectorAll('.box').forEach(function (box, i) {\n"
                "  box.animate(\n"
                "    [{ transform: 'translateY(0px)' }, { transform: 'translateY(-24px)' }],\n"
                "    { duration: 900, delay: i * 200, iterations: Infinity, direction: 'alternate', easing: 'ease-in-out' }\n"
                "  );\n"
                "});\n"
            ),
        },
    ],
    "entrypoint": "index.html",
}


# ----------------------------------------------------------------------
# Child-process scenarios (env is prepared by the parent)
# ----------------------------------------------------------------------

def scenario_web() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    result = workflow.invoke(build_initial_state("build a small website"))
    test_result = result.get("test_result", "")
    files = result.get("files_created", [])

    failures = []
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")
    if "validated" not in test_result.lower():
        failures.append("result does not say the project was validated")
    if "not executed" not in test_result.lower():
        failures.append("result does not say the project was not executed")
    if "OUTPUT:" in test_result:
        failures.append("result looks like a Python execution (has OUTPUT:)")
    if any(name.endswith(".py") for name in files):
        failures.append(f"a Python file was created for a web project: {files}")
    for rel in ("index.html", "styles.css", "script.js"):
        if not (workspace / rel).is_file():
            failures.append(f"missing generated file: {rel}")
    return failures


def scenario_blocked() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    # Phrased as a build on purpose: the literal "delete all files"
    # is a direct workspace action now (scenario deleteall) and never
    # reaches the developer. This scenario guards the tester's gate
    # against GENERATED destructive code, whatever the request was.
    result = workflow.invoke(build_initial_state("create a file cleanup script"))
    test_result = result.get("test_result", "")

    failures = []
    if not test_result.startswith("BLOCKED"):
        failures.append(f"expected BLOCKED result, got:\n{test_result}")
    if "os.remove" not in test_result:
        failures.append("result does not name the pattern found (os.remove)")
    if result.get("retry_count", 0) != 0:
        failures.append(
            "debugger retried a BLOCKED run "
            f"(retry_count={result.get('retry_count')})"
        )
    if not (workspace / "main.py").is_file():
        failures.append("main.py is gone: the destructive script was executed")
    return failures


def scenario_gui() -> list[str]:
    from app.gui import RunManager

    workspace = Path(os.environ["MACA_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    stale = workspace / "stale-from-old-run.txt"
    stale.write_text("leftover", encoding="utf-8")

    manager = RunManager()
    manager._run("build a small website")

    failures = []
    if stale.exists():
        failures.append("GUI run did not clear the workspace (stale file remains)")
    cleared_events = [
        event
        for event in manager.events
        if event.get("type") == "log" and "Workspace cleared" in event.get("message", "")
    ]
    if not cleared_events:
        failures.append("GUI run did not emit the 'Workspace cleared' log event")
    summary = manager.last_summary or {}
    test_result = summary.get("test_result", "")
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"GUI run did not pass validation, got:\n{test_result}")
    if not (workspace / "index.html").is_file():
        failures.append("GUI run did not create index.html")
    return failures


def scenario_entryfix_web() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    # Must not raise: the old code crashed here with a ValueError.
    result = workflow.invoke(build_initial_state("build a small website"))
    test_result = result.get("test_result", "")

    failures = []
    if result.get("entrypoint") != "index.html":
        failures.append(
            f"entrypoint not corrected to index.html: {result.get('entrypoint')!r}"
        )
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")
    if "not executed" not in test_result.lower():
        failures.append("result does not say the project was not executed")
    if "Entrypoint corrected: main.py -> index.html" not in test_result:
        failures.append("correction note missing from the result text")
    if "must never be the web entrypoint" in test_result:
        failures.append("main.py was executed for a web project")
    if not (workspace / "index.html").is_file():
        failures.append("index.html missing from the workspace")
    return failures


def scenario_entryfix_missing() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    result = workflow.invoke(
        build_initial_state("create a python script that prints a greeting")
    )
    test_result = result.get("test_result", "")

    failures = []
    if result.get("entrypoint") != "main.py":
        failures.append(
            f"entrypoint not corrected to main.py: {result.get('entrypoint')!r}"
        )
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")
    if "hello from the real main" not in test_result:
        failures.append("main.py output missing: the Python test did not run it")
    if "Entrypoint corrected: app.py -> main.py" not in test_result:
        failures.append("correction note missing from the result text")
    return failures


def scenario_entryfix_empty() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    # Must not raise: an empty payload ends the run gracefully.
    result = workflow.invoke(build_initial_state("build something"))
    test_result = result.get("test_result", "")
    final_response = result.get("final_response", "")

    failures = []
    if not test_result.startswith("STATUS: FAILED"):
        failures.append(f"expected FAILED result, got:\n{test_result}")
    if "unusable payload" not in test_result:
        failures.append("result does not explain the unusable payload")
    if not final_response.startswith("STATUS: FAILED"):
        failures.append("final_response does not carry the FAILED result")
    if result.get("files_created"):
        failures.append(f"files_created should be empty: {result.get('files_created')}")
    if result.get("retry_count", 0) != 0:
        failures.append("the debugger was invoked for an unusable payload")
    return failures


def scenario_listcontent() -> list[str]:
    # The agents bind their models from app.config at import time, so
    # the list-content fakes are installed BEFORE the workflow (and
    # therefore the agents) are imported.
    import app.config as config
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from app.text_utils import to_text

    failures = []

    # The helper itself, on every shape a model can produce.
    if to_text("plain") != "plain":
        failures.append("to_text changed a plain string")
    if to_text(None) != "":
        failures.append("to_text(None) is not ''")
    blocks = [{"type": "text", "text": "pla"}, {"type": "text", "text": "nner"}]
    if to_text(blocks) != "planner":
        failures.append(f"to_text did not join content blocks: {to_text(blocks)!r}")
    if to_text(["line one", "line two"], joiner="\n") != "line one\nline two":
        failures.append("to_text did not join a list of lines")
    if to_text([{"type": "image", "data": "..."}]) != "":
        failures.append("to_text should ignore text-less blocks")

    def fake_get_llm(role="generic", temperature=0):
        text = {
            "supervisor": "planner",
            "planner": LISTCONTENT_PLAN,
        }.get(role, "ok")
        # LangChain 1.x content-block shape: a list, not a string.
        return RunnableLambda(
            lambda _prompt, _text=text: AIMessage(
                content=[{"type": "text", "text": _text}]
            )
        )

    def fake_get_structured_llm(schema):
        payload = json.loads(os.environ["MACA_FAKE_FILES_JSON"])
        return RunnableLambda(lambda _prompt: schema(**payload))

    config.get_llm = fake_get_llm
    config.get_structured_llm = fake_get_structured_llm

    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    # Must not raise AttributeError: 'list' object has no attribute
    # 'strip' (the supervisor) or fail validation on the list-typed
    # file content (the developer).
    result = workflow.invoke(build_initial_state(LISTCONTENT_REQUEST))
    test_result = result.get("test_result", "")

    plan = result.get("plan", "")
    if not isinstance(plan, str):
        failures.append(f"plan in state is not text: {type(plan).__name__}")
    elif "three coloured boxes" not in plan:
        failures.append(f"plan text lost in coercion: {plan!r}")
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")
    if "not executed" not in test_result.lower():
        failures.append("result does not say the project was not executed")
    for rel in ("index.html", "styles.css", "script.js"):
        if not (workspace / rel).is_file():
            failures.append(f"missing generated file: {rel}")
    if (workspace / "index.html").is_file():
        html = (workspace / "index.html").read_text(encoding="utf-8")
        if 'class="box red"' not in html:
            failures.append("index.html lost its boxes during list coercion")
        if html.lstrip().startswith("["):
            failures.append("index.html looks like a raw list repr, not text")
    return failures


def _snapshot(workspace: Path) -> dict[str, str]:
    """Map of workspace-relative path -> content, caches excluded.

    Running a Python entrypoint legitimately creates __pycache__, so
    byte-for-byte comparisons of the project itself ignore it.
    """

    snapshot = {}

    for path in sorted(workspace.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            snapshot[path.relative_to(workspace).as_posix()] = path.read_text(
                encoding="utf-8"
            )

    return snapshot


def scenario_runexisting() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text(EXISTING_MAIN_PY, encoding="utf-8")
    (workspace / "helper.py").write_text(EXISTING_HELPER_PY, encoding="utf-8")
    before = _snapshot(workspace)

    result = workflow.invoke(build_initial_state("run the code"))
    test_result = result.get("test_result", "")

    failures = []
    if result.get("test_only") is not True:
        failures.append(f"run not flagged test_only: {result.get('test_only')!r}")
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")
    if "hello from the existing project" not in test_result:
        failures.append("existing project's output missing from the result")
    if "existing project ran OK" not in test_result:
        failures.append("existing main.py did not run to completion")
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran and regenerated the project")
    if "developer_was_here.py" in (result.get("files_created") or []):
        failures.append("files_created names the developer's marker file")
    after = _snapshot(workspace)
    if after != before:
        failures.append(
            f"existing files changed by a run-only request:\n"
            f"before={sorted(before)} after={sorted(after)}"
        )
    return failures


def scenario_runexisting_web() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "index.html").write_text(EXISTING_INDEX_HTML, encoding="utf-8")
    (workspace / "styles.css").write_text(
        "body { font-family: sans-serif; }\n", encoding="utf-8"
    )
    (workspace / "script.js").write_text(
        "console.log('existing site');\n", encoding="utf-8"
    )
    before = _snapshot(workspace)

    result = workflow.invoke(build_initial_state("run the code"))
    test_result = result.get("test_result", "")

    failures = []
    if result.get("test_only") is not True:
        failures.append(f"run not flagged test_only: {result.get('test_only')!r}")
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")
    if "not executed" not in test_result.lower():
        failures.append("web project was executed instead of validated")
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran and regenerated the project")
    after = _snapshot(workspace)
    if after != before:
        failures.append(
            f"existing files changed by a run-only request:\n"
            f"before={sorted(before)} after={sorted(after)}"
        )
    return failures


def _populate(workspace: Path, files: dict[str, str]) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (workspace / name).write_text(content, encoding="utf-8")


def _backup_root(workspace: Path) -> Path:
    return workspace.parent / f"{workspace.name}_backups"


def scenario_deletenamed() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "main.py": "print('keep me')\n",
            "notes.txt": "scratch notes\n",
        },
    )

    result = workflow.invoke(build_initial_state("delete notes.txt"))
    outcome = result.get("final_response", "")

    failures = []
    if (workspace / "notes.txt").exists():
        failures.append("notes.txt is still in the workspace")
    if not (workspace / "main.py").is_file():
        failures.append("main.py was removed by a named delete of notes.txt")
    elif (workspace / "main.py").read_text(encoding="utf-8") != "print('keep me')\n":
        failures.append("main.py content changed")
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran for a direct delete request")
    if result.get("files_created"):
        failures.append(f"files were generated: {result.get('files_created')}")
    if "Deleted from workspace: notes.txt" not in outcome:
        failures.append(f"outcome does not report the deletion:\n{outcome}")
    if result.get("test_result", "") != outcome:
        failures.append("test_result does not carry the delete outcome")
    return failures


def scenario_deleteall() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "main.py": "print('one')\n",
            "notes.txt": "two\n",
        },
    )

    result = workflow.invoke(build_initial_state("delete all files"))
    outcome = result.get("final_response", "")

    failures = []
    remaining = _snapshot(workspace)
    if remaining:
        failures.append(f"workspace not emptied: {sorted(remaining)}")
    backup_root = _backup_root(workspace)
    for name, content in (("main.py", "print('one')\n"), ("notes.txt", "two\n")):
        archived = sorted(backup_root.glob(f"*/{name}"))
        if not archived:
            failures.append(f"{name} was not moved under {backup_root}")
        elif archived[0].read_text(encoding="utf-8") != content:
            failures.append(f"archived {name} content changed")
    if "Moved 2 item(s) to backup" not in outcome:
        failures.append(f"outcome does not report the backup move:\n{outcome}")
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran for a direct delete request")
    return failures


def scenario_deletevague() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "main.py": "print('one')\n",
            "notes.txt": "two\n",
        },
    )
    before = _snapshot(workspace)

    result = workflow.invoke(build_initial_state("delete the existing file"))
    outcome = result.get("final_response", "")

    failures = []
    if _snapshot(workspace) != before:
        failures.append("files changed for an ambiguous delete request")
    if _backup_root(workspace).exists():
        failures.append("a backup was created for an ambiguous delete request")
    if "Nothing was deleted" not in outcome:
        failures.append(f"outcome does not say nothing was deleted:\n{outcome}")
    for name in ("main.py", "notes.txt"):
        if name not in outcome:
            failures.append(f"outcome does not list {name}")
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran for a direct delete request")
    return failures


def scenario_deleteone() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(workspace, {"main.py": "print('only file')\n"})

    result = workflow.invoke(build_initial_state("delete the existing file"))
    outcome = result.get("final_response", "")

    failures = []
    if (workspace / "main.py").exists():
        failures.append("the single existing file is still in the workspace")
    archived = sorted(_backup_root(workspace).glob("*/main.py"))
    if not archived:
        failures.append("the file was not moved into the backup folder")
    elif archived[0].read_text(encoding="utf-8") != "print('only file')\n":
        failures.append("archived main.py content changed")
    if "Moved 1 item(s) to backup" not in outcome:
        failures.append(f"outcome does not report the backup move:\n{outcome}")
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran for a direct delete request")
    return failures


def scenario_question() -> list[str]:
    from app.graph.workflow import workflow
    from app.intent import is_delete_request, is_question, is_test_only_request
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "index.html": EXISTING_INDEX_HTML,
            "styles.css": "body { font-family: sans-serif; }\n",
            "script.js": "console.log('existing site');\n",
        },
    )
    before = _snapshot(workspace)

    failures = []
    # Classification: questions are classified before commands, so
    # the "run" inside the question matches nothing.
    if not is_question("how to run it?"):
        failures.append("'how to run it?' not classified as a question")
    if is_test_only_request("how to run it?"):
        failures.append("'how to run it?' still classified as test-only")
    if is_delete_request("how to run it?"):
        failures.append("'how to run it?' classified as a delete request")

    result = workflow.invoke(build_initial_state("how to run it?"))
    answer = result.get("final_response", "")

    if "STATUS:" in answer:
        failures.append(
            f"a tester result was produced for a question:\n{answer}"
        )
    lowered = answer.lower()
    if "open" not in lowered or "index.html" not in lowered or "browser" not in lowered:
        failures.append(
            "answer does not explain opening index.html in a browser:"
            f"\n{answer}"
        )
    if result.get("files_created"):
        failures.append(
            f"files were generated for a question: {result.get('files_created')}"
        )
    if (workspace / "developer_was_here.py").exists():
        failures.append("the developer ran for a question")
    if _snapshot(workspace) != before:
        failures.append("workspace files changed for a question")
    if _backup_root(workspace).exists():
        failures.append("a backup was created for a question (workspace cleared?)")

    # The GUI route must answer too, without touching the workspace.
    from app.gui import RunManager

    manager = RunManager()
    manager._run("how to run it?")
    summary = manager.last_summary or {}
    gui_answer = summary.get("final_response", "")
    gui_lowered = gui_answer.lower()
    if "index.html" not in gui_lowered or "browser" not in gui_lowered:
        failures.append(
            f"GUI answer does not explain the how-to-run facts:\n{gui_answer}"
        )
    if _snapshot(workspace) != before:
        failures.append("workspace files changed by the GUI question run")
    if _backup_root(workspace).exists():
        failures.append("a backup was created by the GUI question run")
    return failures


def scenario_questionbuild() -> list[str]:
    from app.graph.workflow import workflow
    from app.intent import is_question
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])

    failures = []
    if is_question("create a login page"):
        failures.append("a plain build order was classified as a question")
    if not is_question("how do I create a login page?"):
        failures.append(
            "'how do I create a login page?' not classified as a question"
        )

    # A normal build request still builds (guard against the
    # question detector over-blocking real orders).
    result = workflow.invoke(
        build_initial_state("create a python script that prints a greeting")
    )
    test_result = result.get("test_result", "")
    if not (workspace / "main.py").is_file():
        failures.append("build request did not create main.py")
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"build request did not pass:\n{test_result}")
    if "built by the agents" not in test_result:
        failures.append("build request did not run the built script")

    # A question ABOUT building gets an answer, not a build.
    before = _snapshot(workspace)
    result = workflow.invoke(build_initial_state("how do I create a login page?"))
    answer = result.get("final_response", "")
    if not answer.strip():
        failures.append("question about building got an empty answer")
    if "STATUS:" in answer:
        failures.append(
            f"question about building produced a tester result:\n{answer}"
        )
    if _snapshot(workspace) != before:
        failures.append("a question about building changed the workspace")
    return failures


def scenario_preview() -> list[str]:
    from fastapi.testclient import TestClient

    import app.gui as gui

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "index.html": (
                "<!DOCTYPE html>\n"
                "<html><head>"
                '<link rel="stylesheet" href="style.css">'
                "</head><body><h1>Preview marker 12345</h1>"
                "</body></html>\n"
            ),
            "style.css": "h1 { color: rgb(1, 2, 3); }\n",
        },
    )

    failures = []
    client = TestClient(gui.app)

    response = client.get("/preview/index.html")
    if response.status_code != 200:
        failures.append(f"GET /preview/index.html -> {response.status_code}")
    elif "Preview marker 12345" not in response.text:
        failures.append("preview did not serve the workspace index.html")

    response = client.get("/preview/style.css")
    if response.status_code != 200:
        failures.append(f"GET /preview/style.css -> {response.status_code}")
    elif "color: rgb(1, 2, 3)" not in response.text:
        failures.append("preview did not serve the workspace style.css")

    # Traversal attempts must never reach the application's source.
    for attempt in (
        "/preview/../app/gui.py",
        "/preview/%2e%2e/app/gui.py",
        "/preview/..%2fapp%2fgui.py",
        "/preview/%2e%2e%2fapp%2fgui.py",
    ):
        response = client.get(attempt)
        if response.status_code == 200 and "RunManager" in response.text:
            failures.append(
                f"traversal attempt leaked application source: {attempt}"
            )
        if response.status_code not in (400, 404):
            failures.append(
                f"traversal attempt {attempt} -> {response.status_code}, "
                "expected 400 or 404"
            )

    response = client.get("/preview/no-such-file.txt")
    if response.status_code != 404:
        failures.append(
            f"missing preview file -> {response.status_code}, expected 404"
        )

    response = client.get("/api/preview-info")
    if response.status_code != 200:
        failures.append(f"GET /api/preview-info -> {response.status_code}")
    else:
        info = response.json()
        if not info.get("available") or info.get("url") != "/preview/index.html":
            failures.append(f"preview-info does not point at index.html: {info}")
    return failures


def scenario_deletegui() -> list[str]:
    from app.gui import RunManager

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "main.py": "print('keep me')\n",
            "notes.txt": "scratch notes\n",
        },
    )

    manager = RunManager()
    manager._run("delete notes.txt")

    failures = []
    if (workspace / "notes.txt").exists():
        failures.append("GUI run did not delete notes.txt")
    if not (workspace / "main.py").is_file():
        failures.append("GUI run removed main.py as well")
    summary = manager.last_summary or {}
    if not summary.get("test_result", "").startswith(
        "Deleted from workspace: notes.txt"
    ):
        failures.append(
            f"GUI summary does not carry the outcome: {summary.get('test_result')!r}"
        )
    if not any(
        event.get("type") == "run_finished" for event in manager.events
    ):
        failures.append("GUI run did not emit run_finished")
    return failures


def _install_dynamic_payload_factory() -> Path:
    """Patch the fake structured model to read its payload per call.

    The developer's structured model is built once, at import time,
    from the environment payload — one payload per process. The
    edit-follow-up scenario needs run 2 to return a DIFFERENT
    payload from run 1 inside one process, so the factory is patched
    (before any agent module is imported, exactly as scenario
    listcontent patches config) to read the payload from a JSON file
    the scenario rewrites between runs.
    """

    import app.config as config
    from langchain_core.runnables import RunnableLambda

    payload_path = (
        Path(tempfile.mkdtemp(prefix="maca-payload-")) / "payload.json"
    )

    def fake_get_structured_llm(schema):
        def build(_prompt):
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            return schema(**payload)

        return RunnableLambda(build)

    config.get_structured_llm = fake_get_structured_llm
    return payload_path


def scenario_editfollowup() -> list[str]:
    payload_path = _install_dynamic_payload_factory()

    from app.gui import RunManager

    workspace = Path(os.environ["MACA_WORKSPACE"])
    failures = []

    # Run 1: a normal creation request builds the page.
    payload_path.write_text(json.dumps(EDIT_V1_PAYLOAD), encoding="utf-8")
    manager = RunManager()
    manager._run("create a webpage with a heading")

    summary1 = manager.last_summary or {}
    if summary1.get("edit_mode") is not False:
        failures.append(
            f"run 1 should be a fresh build: edit_mode={summary1.get('edit_mode')!r}"
        )
    if not summary1.get("test_result", "").startswith("STATUS: PASSED"):
        failures.append(f"run 1 did not pass:\n{summary1.get('test_result')}")
    if not (workspace / "index.html").is_file():
        failures.append("run 1 did not create index.html")
    if (workspace / "index.html").is_file() and "<footer>" in (
        workspace / "index.html"
    ).read_text(encoding="utf-8"):
        failures.append("run 1 index.html already has a footer")
    css_before = (workspace / "style.css").read_bytes()
    events_before = len(manager.events)

    # Run 2: the follow-up. The fake developer returns ONLY the
    # updated index.html; style.css is not part of the payload.
    payload_path.write_text(json.dumps(EDIT_V2_PAYLOAD), encoding="utf-8")
    manager._run("add a footer to the page")

    summary2 = manager.last_summary or {}
    if summary2.get("edit_mode") is not True:
        failures.append(
            f"run 2 was not an edit run: edit_mode={summary2.get('edit_mode')!r}"
        )
    if not summary2.get("test_result", "").startswith("STATUS: PASSED"):
        failures.append(f"run 2 did not pass:\n{summary2.get('test_result')}")
    run2_events = manager.events[events_before:]
    if any(
        event.get("type") == "log"
        and "Workspace cleared" in event.get("message", "")
        for event in run2_events
    ):
        failures.append("run 2 cleared the workspace (backup-and-rebuild path)")
    if not any(
        event.get("type") == "log"
        and "Editing the existing project" in event.get("message", "")
        for event in run2_events
    ):
        failures.append("run 2 did not announce edit mode in its log")
    if _backup_root(workspace).exists():
        failures.append("a backup folder was created by the edit run")
    if (workspace / "style.css").read_bytes() != css_before:
        failures.append("style.css changed during the edit run (must be byte-identical)")
    html_after = (workspace / "index.html").read_text(encoding="utf-8")
    if "<footer>Added by the follow-up</footer>" not in html_after:
        failures.append("index.html does not contain the added footer")
    if "<h1>Editable page</h1>" not in html_after:
        failures.append("the edit lost the existing page content")
    return failures


def scenario_freshstart() -> list[str]:
    from app.gui import RunManager

    workspace = Path(os.environ["MACA_WORKSPACE"])
    _populate(
        workspace,
        {
            "index.html": EXISTING_INDEX_HTML,
            "styles.css": "body { font-family: sans-serif; }\n",
        },
    )

    manager = RunManager()
    manager._run("start over and make a new project")
    summary = manager.last_summary or {}

    failures = []
    if summary.get("edit_mode") is not False:
        failures.append(
            f"fresh start ran in edit mode: edit_mode={summary.get('edit_mode')!r}"
        )
    if (workspace / "index.html").exists():
        failures.append("the old index.html is still in the workspace")
    if not (workspace / "main.py").is_file():
        failures.append("the new project's main.py was not created")
    archived = sorted(_backup_root(workspace).glob("*/index.html"))
    if not archived:
        failures.append("the old project was not moved to the backup folder")
    if not summary.get("test_result", "").startswith("STATUS: PASSED"):
        failures.append(f"fresh start run did not pass:\n{summary.get('test_result')}")
    return failures


def scenario_nocomments() -> list[str]:
    from app.graph.workflow import workflow
    from app.main import build_initial_state

    workspace = Path(os.environ["MACA_WORKSPACE"])
    result = workflow.invoke(build_initial_state("build a small website"))
    test_result = result.get("test_result", "")

    failures = []
    if not test_result.startswith("STATUS: PASSED"):
        failures.append(f"expected PASSED, got:\n{test_result}")

    html = (workspace / "index.html").read_text(encoding="utf-8")
    if "<!--" in html or "page comment" in html:
        failures.append(f"HTML comment survived:\n{html}")

    css = (workspace / "style.css").read_text(encoding="utf-8")
    if "/*" in css or "comment" in css:
        failures.append(f"CSS comment survived:\n{css}")

    python = (workspace / "main.py").read_text(encoding="utf-8")
    for marker in ("header comment", "trailing comment", "standalone comment"):
        if marker in python:
            failures.append(f"Python comment survived ({marker}):\n{python}")
    if '"keep # this"' not in python:
        failures.append(
            f"the '#' string literal was damaged by stripping:\n{python}"
        )
    try:
        compile(python, "main.py", "exec")
    except SyntaxError as exc:
        failures.append(f"stripped main.py no longer compiles: {exc}")
    return failures


_PHASES_DRIVER = '''
import json
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1]

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    page.goto(url)
    page.fill("#request-input", "build a small website")
    page.click("#run-button")
    page.wait_for_function(
        "document.querySelector('#chat-log').innerText.includes('Done')",
        timeout=120000,
    )
    data = {
        "chat": page.inner_text("#chat-log"),
        "terminal": page.inner_text("#terminal"),
        "body": page.inner_text("body"),
    }
    browser.close()

print(json.dumps(data))
'''


def _playwright_python() -> str | None:
    """A python interpreter that can import playwright, if one exists."""

    candidates = [
        Path.home() / "workspace" / "book2-venv" / "bin" / "python",
        Path(sys.executable),
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import playwright"],
            capture_output=True,
            timeout=60,
        )
        if probe.returncode == 0:
            return str(candidate)

    return None


def scenario_phases() -> list[str]:
    failures = []

    driver_python = _playwright_python()
    if driver_python is None:
        return ["no python with playwright available for the browser driver"]

    port = 8791
    server = subprocess.Popen(
        [
            PY,
            "-c",
            (
                "import uvicorn, app.gui; "
                f"uvicorn.run(app.gui.app, host='127.0.0.1', port={port}, "
                "log_level='error')"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    driver_path = (
        Path(tempfile.mkdtemp(prefix="maca-phases-")) / "driver.py"
    )
    driver_path.write_text(_PHASES_DRIVER, encoding="utf-8")

    try:
        import time
        import urllib.request

        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/status", timeout=2
                ) as response:
                    ready = response.status == 200
                if ready:
                    break
            except Exception:
                time.sleep(0.5)
        if not ready:
            return ["the GUI server did not come up on the test port"]

        proc = subprocess.run(
            [driver_python, str(driver_path), f"http://127.0.0.1:{port}/"],
            capture_output=True,
            text=True,
            timeout=240,
        )
        if proc.returncode != 0:
            return [
                f"browser driver failed:\n{proc.stdout[-1000:]}\n{proc.stderr[-1500:]}"
            ]

        data = json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    chat = data.get("chat", "")
    terminal = data.get("terminal", "")
    body = data.get("body", "")

    for word in ("Thinking", "Building", "Testing", "Done"):
        if word not in chat:
            failures.append(f"chat stream is missing the phase line {word!r}:\n{chat}")
        if word not in terminal:
            failures.append(f"terminal is missing {word!r}:\n{terminal}")
    for banned in ("MODE:", "CHECKS:", "STATUS:"):
        if banned in body:
            failures.append(f"verbose tester text rendered in the page: {banned!r}")
    if "Routed to" in chat or "plan created" in chat.lower():
        failures.append(f"agent prose leaked into the chat stream:\n{chat}")
    return failures


SCENARIOS = {
    "web": scenario_web,
    "blocked": scenario_blocked,
    "gui": scenario_gui,
    "entryfix_web": scenario_entryfix_web,
    "entryfix_missing": scenario_entryfix_missing,
    "entryfix_empty": scenario_entryfix_empty,
    "listcontent": scenario_listcontent,
    "runexisting": scenario_runexisting,
    "runexisting_web": scenario_runexisting_web,
    "deletenamed": scenario_deletenamed,
    "deleteall": scenario_deleteall,
    "deletevague": scenario_deletevague,
    "deleteone": scenario_deleteone,
    "deletegui": scenario_deletegui,
    "question": scenario_question,
    "questionbuild": scenario_questionbuild,
    "preview": scenario_preview,
    "editfollowup": scenario_editfollowup,
    "freshstart": scenario_freshstart,
    "nocomments": scenario_nocomments,
    "phases": scenario_phases,
}


# ----------------------------------------------------------------------
# Parent driver
# ----------------------------------------------------------------------

def _base_env(workspace: str, payload: dict | None) -> dict:
    env = dict(os.environ)
    env["MACA_MODEL_PROVIDER"] = "fake"
    env["MACA_WORKSPACE"] = workspace
    if payload is not None:
        env["MACA_FAKE_FILES_JSON"] = json.dumps(payload)
    else:
        env.pop("MACA_FAKE_FILES_JSON", None)
    return env


def _run_subprocess(command: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )


def _report(name: str, proc: subprocess.CompletedProcess) -> bool:
    ok = proc.returncode == 0
    print(f"\n===== scenario '{name}': {'PASSED' if ok else 'FAILED'} =====")
    output = (proc.stdout or "").strip()
    if output:
        print(output[-3000:])
    if not ok:
        stderr = (proc.stderr or "").strip()
        if stderr:
            print("[stderr tail]")
            print(stderr[-2000:])
    return ok


def main() -> int:
    results = {}

    # 1 + 2 + 5: child-process scenarios driven by a fake payload.
    for name, payload in (
        ("web", WEB_PAYLOAD),
        ("blocked", BLOCKED_PAYLOAD),
        ("gui", WEB_PAYLOAD),
        ("entryfix_web", ENTRYFIX_WEB_PAYLOAD),
        ("entryfix_missing", ENTRYFIX_MISSING_PAYLOAD),
        ("entryfix_empty", ENTRYFIX_EMPTY_PAYLOAD),
        ("listcontent", LISTCONTENT_PAYLOAD),
        ("runexisting", DEVELOPER_MARKER_PAYLOAD),
        ("runexisting_web", DEVELOPER_MARKER_PAYLOAD),
        # Delete scenarios: the marker payload exposes any developer
        # run, which a direct delete request must never trigger.
        ("deletenamed", DEVELOPER_MARKER_PAYLOAD),
        ("deleteall", DEVELOPER_MARKER_PAYLOAD),
        ("deletevague", DEVELOPER_MARKER_PAYLOAD),
        ("deleteone", DEVELOPER_MARKER_PAYLOAD),
        ("deletegui", DEVELOPER_MARKER_PAYLOAD),
        # Question scenarios: the marker payload exposes any
        # developer run for the "how to run it?" question; the
        # question-about-building scenario uses a real build payload
        # for its build half. Preview needs no model at all.
        ("question", DEVELOPER_MARKER_PAYLOAD),
        ("questionbuild", QUESTIONBUILD_PAYLOAD),
        ("preview", None),
        # Edit-mode scenarios: the follow-up scenario swaps its own
        # payload between runs (see _install_dynamic_payload_factory);
        # the env payload is its run-1 starting point. Fresh start
        # replaces the populated workspace with the payload project.
        # No-comments checks what the developer writes. Phases drives
        # the served GUI in a real browser.
        ("editfollowup", EDIT_V1_PAYLOAD),
        ("freshstart", FRESHSTART_PAYLOAD),
        ("nocomments", NOCOMMENTS_PAYLOAD),
        ("phases", WEB_PAYLOAD),
    ):
        workspace = tempfile.mkdtemp(prefix=f"maca-{name}-")
        proc = _run_subprocess(
            [PY, str(Path(__file__).resolve()), "--scenario", name],
            _base_env(workspace, payload),
        )
        results[name] = _report(name, proc)

    # 3: the pre-existing smoke test, untouched.
    smoke_env = _base_env(tempfile.mkdtemp(prefix="maca-smoke-"), None)
    smoke_env.pop("MACA_WORKSPACE", None)  # smoke test picks its own
    proc = _run_subprocess(
        [PY, str(PROJECT_ROOT / "scripts" / "smoke_test.py")], smoke_env
    )
    results["smoke"] = _report("smoke", proc)

    # 4: two CLI runs in one shared workspace.
    workspace = tempfile.mkdtemp(prefix="maca-tworuns-")
    tworuns_ok = True
    detail = []
    for payload, marker in ((FIRST_PAYLOAD, "first.py"), (SECOND_PAYLOAD, "second.py")):
        proc = _run_subprocess(
            [PY, "-m", "app.main", f"run the {marker} task"],
            _base_env(workspace, payload),
        )
        if proc.returncode != 0 or "STATUS: PASSED" not in proc.stdout:
            tworuns_ok = False
            detail.append(f"CLI run for {marker} failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
            break
        if not (Path(workspace) / marker).is_file():
            tworuns_ok = False
            detail.append(f"{marker} missing after its CLI run")
            break
        detail.append(f"after run: {marker} present")
    if tworuns_ok:
        if (Path(workspace) / "first.py").exists():
            tworuns_ok = False
            detail.append("first.py survived the second run (workspace not cleared)")
        else:
            detail.append("after run 2: first.py gone, second.py present")
    print(f"\n===== scenario 'tworuns': {'PASSED' if tworuns_ok else 'FAILED'} =====")
    for line in detail:
        print(line)
    results["tworuns"] = tworuns_ok

    # 12: a build run over an occupied workspace archives the old
    # project into a timestamped backup folder instead of deleting it.
    workspace = tempfile.mkdtemp(prefix="maca-backup-")
    ws_path = Path(workspace)
    (ws_path / "old_project.py").write_text(
        "print('old project')\n", encoding="utf-8"
    )
    proc = _run_subprocess(
        [PY, "-m", "app.main", "create a second script"],
        _base_env(workspace, SECOND_PAYLOAD),
    )
    backup_ok = proc.returncode == 0 and "STATUS: PASSED" in proc.stdout
    detail = []
    if not backup_ok:
        detail.append(
            f"CLI build run failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    else:
        if (ws_path / "old_project.py").exists():
            backup_ok = False
            detail.append("old_project.py still sits in the workspace")
        backup_root = ws_path.parent / f"{ws_path.name}_backups"
        archived = (
            sorted(backup_root.glob("*/old_project.py"))
            if backup_root.is_dir()
            else []
        )
        if not archived:
            backup_ok = False
            detail.append(
                f"old_project.py was not archived under {backup_root} "
                "(it must be moved, never deleted)"
            )
        else:
            detail.append(f"old_project.py archived at {archived[0]}")
            content = archived[0].read_text(encoding="utf-8")
            if content != "print('old project')\n":
                backup_ok = False
                detail.append("archived old_project.py content changed")
        if "moved to backup" not in proc.stdout:
            backup_ok = False
            detail.append(
                "CLI output does not say where the previous project went"
            )
        if not (ws_path / "second.py").is_file():
            backup_ok = False
            detail.append("second.py missing after its build run")
    print(f"\n===== scenario 'backup': {'PASSED' if backup_ok else 'FAILED'} =====")
    for line in detail:
        print(line)
    results["backup"] = backup_ok

    # 18: the CLI entry point performs a named delete directly,
    # without running the agent graph at all.
    workspace = tempfile.mkdtemp(prefix="maca-deletecli-")
    ws_path = Path(workspace)
    (ws_path / "main.py").write_text("print('keep me')\n", encoding="utf-8")
    (ws_path / "notes.txt").write_text("scratch notes\n", encoding="utf-8")
    proc = _run_subprocess(
        [PY, "-m", "app.main", "delete notes.txt"],
        _base_env(workspace, None),
    )
    deletecli_ok = proc.returncode == 0
    detail = []
    if not deletecli_ok:
        detail.append(
            f"CLI delete run failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    else:
        if "Deleted from workspace: notes.txt" not in proc.stdout:
            deletecli_ok = False
            detail.append("CLI output does not report the deletion")
        if (ws_path / "notes.txt").exists():
            deletecli_ok = False
            detail.append("notes.txt still in the workspace after the CLI run")
        if not (ws_path / "main.py").is_file():
            deletecli_ok = False
            detail.append("main.py was removed by the CLI delete run")
        if deletecli_ok:
            detail.append("CLI deleted notes.txt directly; main.py untouched")
    print(f"\n===== scenario 'deletecli': {'PASSED' if deletecli_ok else 'FAILED'} =====")
    for line in detail:
        print(line)
    results["deletecli"] = deletecli_ok

    print("\n================ SUMMARY ================")
    for name, ok in results.items():
        print(f"  {name}: {'PASSED' if ok else 'FAILED'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--scenario":
        scenario_name = sys.argv[2]
        os.environ["MACA_MODEL_PROVIDER"] = "fake"
        failures = SCENARIOS[scenario_name]()
        for failure in failures:
            print(f"[{scenario_name}] FAIL: {failure}")
        if not failures:
            print(f"[{scenario_name}] all assertions passed")
        raise SystemExit(1 if failures else 0)
    raise SystemExit(main())
