"""Command-line entry point for the multi-agent coding assistant.

Usage (from the project root):

    python -m app.main "create a python script that prints the fibonacci sequence"
    python -m app.main            # prompts for the request instead

For the graphical interface see:  python -m app.gui
"""

import argparse
import sys

from app.agents.answerer import answer_question
from app.config import get_workspace_root
from app.direct_actions import handle_delete_request
from app.graph.workflow import workflow
from app.intent import is_question, is_test_only_request
from app.tools.workspace_tools import clear_workspace


def build_initial_state(user_request: str) -> dict:
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
        "final_response": "",
    }


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

    if is_test_only_request(user_request):
        # Run/test-only: the existing workspace IS the subject of the
        # request. It is kept exactly as it is.
        print(
            "[MAIN] Run/test-only request: keeping the existing "
            "workspace as-is (nothing cleared)."
        )
    else:
        # Start every build run from an empty workspace so files from
        # previous runs cannot pollute this one. The previous project
        # is not deleted: it is moved to a timestamped backup folder.
        cleared = clear_workspace()
        if cleared.backup_dir is not None:
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

    print(f"[MAIN] Request: {user_request}")

    result = workflow.invoke(build_initial_state(user_request))

    print("\n================ RESULT ================")
    if result.get("plan"):
        print("\nPLAN:\n" + result["plan"])
    print("\nFILES CREATED:")
    for path in result.get("files_created", []):
        print(f"  - {path}")
    print("\nTEST RESULT:")
    print(result.get("test_result") or "(no test result)")
    if result.get("final_response"):
        print("\nFINAL RESPONSE:")
        print(result["final_response"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
