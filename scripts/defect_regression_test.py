"""Regression tests for the defects fixed on 2026-10-23.

Scenarios 1-5 cover the original three defects (web projects forced
through Python, destructive generated code executed by the tester,
stale files polluting the workspace). Scenarios 6-8 cover the
follow-up crash: a wrong entrypoint in the developer payload used to
raise a ValueError that killed the whole run; it is now corrected
when unambiguous and reported gracefully when it is not.

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
             leave the first run's files behind.
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
    result = workflow.invoke(build_initial_state("delete all files"))
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


SCENARIOS = {
    "web": scenario_web,
    "blocked": scenario_blocked,
    "gui": scenario_gui,
    "entryfix_web": scenario_entryfix_web,
    "entryfix_missing": scenario_entryfix_missing,
    "entryfix_empty": scenario_entryfix_empty,
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
