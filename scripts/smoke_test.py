"""Offline end-to-end smoke test for the agent graph.

Runs the full supervisor -> planner -> developer -> tester workflow with
the FAKE model provider (no Ollama server, no API key), in a throwaway
workspace, and asserts that:

  1. a file was actually created in the workspace,
  2. the tester produced a "STATUS: PASSED" test_result.

Usage (from the project root):

    .venv/bin/python scripts/smoke_test.py

Exit code 0 on success, 1 on failure.
"""

import os
import sys
import tempfile
from pathlib import Path

# The provider and workspace MUST be set before any app module is imported,
# because agents build their models at import time.
os.environ["MACA_MODEL_PROVIDER"] = "fake"
_SMOKE_WORKSPACE = tempfile.mkdtemp(prefix="maca-smoke-")
os.environ["MACA_WORKSPACE"] = _SMOKE_WORKSPACE

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph.workflow import workflow  # noqa: E402
from app.main import build_initial_state  # noqa: E402


def main() -> int:
    request = "create a python script that prints a greeting"
    print(f"[SMOKE] provider=fake workspace={_SMOKE_WORKSPACE}")
    print(f"[SMOKE] request: {request}")

    result = workflow.invoke(build_initial_state(request))

    files = result.get("files_created", [])
    test_result = result.get("test_result", "")

    print(f"[SMOKE] files_created: {files}")
    print(f"[SMOKE] test_result:\n{test_result}")

    failures = []

    if not files:
        failures.append("no files were created")
    for rel in files:
        if not (Path(_SMOKE_WORKSPACE) / rel).is_file():
            failures.append(f"file missing on disk: {rel}")

    if not test_result.startswith("STATUS: PASSED"):
        failures.append("tester did not report STATUS: PASSED")

    if failures:
        for failure in failures:
            print(f"[SMOKE] FAIL: {failure}")
        return 1

    print("[SMOKE] PASSED: graph ran end to end, file created, tester passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
