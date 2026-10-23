"""Command-line entry point for the multi-agent coding assistant.

Usage (from the project root):

    python -m app.main "create a python script that prints the fibonacci sequence"
    python -m app.main            # prompts for the request instead

For the graphical interface see:  python -m app.gui
"""

import argparse
import sys
from typing import NamedTuple

from app.agents.answerer import answer_question
from app.config import get_workspace_root
from app.direct_actions import handle_delete_request
from app.graph.workflow import workflow
from app.intent import is_edit_request, is_question, is_test_only_request
from app.memory import load_memory, record_run
from app.tools.workspace_tools import (
    ClearResult,
    clear_workspace,
    load_existing_files,
)


def build_initial_state(
    user_request: str,
    *,
    edit_mode: bool = False,
    existing_files: list | None = None,
    project_memory: dict | None = None,
) -> dict:
    """Build a complete, valid AgentState for a new run."""
    return {
        "user_request": user_request,
        "plan": "",
        "files_created": [],
        "entrypoint": "",
        "entrypoint_note": "",
        "test_command": "",
        "working_directory": str(get_workspace_root()),
        "language": "python",
        "framework": "",
        "test_result": "",
        "retry_count": 0,
        "next_agent": "",
        "test_only": is_test_only_request(user_request),
        "edit_mode": edit_mode,
        "existing_files": list(existing_files or []),
        "project_memory": dict(project_memory) if project_memory else {},
        "preservation_retried": False,
        "preservation_note": "",
        "preservation_complaint": "",
        "final_response": "",
    }


class BuildPreparation(NamedTuple):
    """How a build run starts: edit the project, or build fresh.

    edit_mode:      True for a follow-up instruction that modifies
                    the project already in the workspace. The
                    workspace is NOT cleared; existing_files holds
                    its contents for the developer.
    existing_files: [{"path", "content"}, ...] snapshot (edit only).
    cleared:        The clear_workspace() outcome (fresh builds).
    project_memory: The stored project memory (edit only): the
                    instruction history and locked design tokens the
                    planner and developer are held to.
    """

    edit_mode: bool
    existing_files: list
    cleared: ClearResult | None
    project_memory: dict | None = None


def prepare_build(user_request: str) -> BuildPreparation:
    """Decide how a build run starts, and prepare the workspace.

    A follow-up that modifies the existing project ("add a footer",
    "make the circle bigger") runs in EDIT mode: the workspace is
    kept exactly as it is and its files are loaded for the
    developer, together with the persistent project memory. Anything
    else — an empty workspace, a creation request, an explicit
    "start over" — keeps the established behaviour: the previous
    project is moved to a backup folder and the build starts from
    an empty workspace.
    """

    existing = load_existing_files()

    if existing and is_edit_request(user_request):
        return BuildPreparation(
            edit_mode=True,
            existing_files=existing,
            cleared=None,
            project_memory=load_memory(),
        )

    return BuildPreparation(
        edit_mode=False,
        existing_files=[],
        cleared=clear_workspace(),
    )


# The only progress words the CLI prints while the graph runs: what
# the user is watching is agents thinking, then building, then
# testing. Mirrors the GUI's phase lines.
_PHASE_BY_NODE = {
    "supervisor": "Thinking",
    "planner": "Thinking",
    "developer": "Building",
    "debugger": "Building",
    "tester": "Testing",
}


def run_with_phases(initial_state: dict) -> dict:
    """Invoke the workflow, printing each phase as it is entered.

    Returns the merged final state (node updates applied in order,
    exactly what workflow.invoke would return for these fields).
    """

    result = dict(initial_state)
    last_phase = ""

    for chunk in workflow.stream(initial_state, stream_mode="updates"):
        for node, update in chunk.items():
            if update:
                result.update(update)
            phase = _PHASE_BY_NODE.get(node, "")
            if phase and phase != last_phase:
                print(phase)
                last_phase = phase

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="multi-agent-coding-assistant",
        description=(
            "Run the supervisor/planner/developer/tester/debugger "
            "agent graph on a coding request."
        ),
    )
    parser.add_argument(
        "request",
        nargs="*",
        help="What the agents should build, e.g. 'create a todo CLI in python'",
    )
    args = parser.parse_args(argv)

    user_request = " ".join(args.request).strip()

    if not user_request:
        try:
            user_request = input("What should the agents build? ").strip()
        except EOFError:
            user_request = ""

    if not user_request:
        parser.print_usage(sys.stderr)
        print("error: a request is required", file=sys.stderr)
        return 2

    print(f"\n[MAIN] Workspace: {get_workspace_root()}")

    # A question is not a command: answer it from the current
    # workspace state and report the answer as the run's result.
    # Checked FIRST, before the delete and run/test paths, so the
    # verbs inside a question never trigger an action. No agents
    # run, nothing is generated, the workspace is not cleared.
    if is_question(user_request):
        answer = answer_question(user_request)
        print(
            "[MAIN] Question: answered directly "
            "(no agents run, workspace untouched)."
        )
        print(f"[MAIN] Request: {user_request}")
        print("\n================ RESULT ================")
        print(answer)
        return 0

    # A pure delete request is an operation on the workspace, not a
    # build: perform it directly and report the outcome as the run's
    # result. No agents run and nothing is generated.
    delete_outcome = handle_delete_request(user_request)

    if delete_outcome is not None:
        print(
            "[MAIN] Delete request: handled directly "
            "(no agents run, nothing generated)."
        )
        print(f"[MAIN] Request: {user_request}")
        print("\n================ RESULT ================")
        print(delete_outcome)
        return 0

    # Only build/edit runs are recorded in the project memory;
    # question, delete and run/test-only runs are not build
    # instructions and never touch the history.
    build_mode: str | None = None

    if is_test_only_request(user_request):
        # Run/test-only: the existing workspace IS the subject of the
        # request. It is kept exactly as it is.
        print(
            "[MAIN] Run/test-only request: keeping the existing "
            "workspace as-is (nothing cleared)."
        )
        initial_state = build_initial_state(user_request)
    else:
        # A follow-up that modifies the existing project edits it in
        # place: no clearing, no backup, the developer works from the
        # files that are already there. Any other build keeps the
        # established behaviour: the previous project is moved to a
        # timestamped backup folder and the build starts empty.
        prep = prepare_build(user_request)
        if prep.edit_mode:
            build_mode = "edit"
            print(
                f"[MAIN] Editing the existing project "
                f"({len(prep.existing_files)} file(s) loaded; "
                "workspace kept, nothing cleared)."
            )
            initial_state = build_initial_state(
                user_request,
                edit_mode=True,
                existing_files=prep.existing_files,
                project_memory=prep.project_memory,
            )
        else:
            build_mode = "fresh"
            cleared = prep.cleared
            if cleared is not None and cleared.backup_dir is not None:
                print(
                    f"[MAIN] Cleared workspace for the new run "
                    f"({cleared.moved} item(s) moved to backup: "
                    f"{cleared.backup_dir})."
                )
            else:
                print(
                    "[MAIN] Cleared workspace for the new run "
                    "(workspace was already empty)."
                )
            initial_state = build_initial_state(user_request)

    print(f"[MAIN] Request: {user_request}")

    result = run_with_phases(initial_state)

    if build_mode is not None:
        # The persistent memory is what the NEXT run reads: a fresh
        # build starts a new history, an edit extends this project's.
        record_run(
            user_request,
            build_mode,
            success=result.get("test_result", "").startswith(
                "STATUS: PASSED"
            ),
        )

    # One short final line. The detailed tester output stays in the
    # run state; it is not dumped to the terminal.
    print("\n================ RESULT ================")
    test_result = result.get("test_result", "")
    if test_result.startswith("STATUS: PASSED"):
        print("STATUS: PASSED")
    elif test_result.startswith("BLOCKED"):
        print("STATUS: BLOCKED")
    else:
        print("STATUS: FAILED")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
