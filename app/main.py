"""Command-line entry point for the multi-agent coding assistant.

Usage (from the project root):

    python -m app.main "create a python script that prints the fibonacci sequence"
    python -m app.main            # prompts for the request instead

For the graphical interface see:  python -m app.gui
"""

import argparse
import sys

from app.config import get_workspace_root
from app.graph.workflow import workflow
from app.tools.workspace_tools import clear_workspace


def build_initial_state(user_request: str) -> dict:
    """Build a complete, valid AgentState for a new run."""
    return {
        "user_request": user_request,
        "plan": "",
        "files_created": [],
        "entrypoint": "",
        "test_command": "",
        "working_directory": str(get_workspace_root()),
        "language": "python",
        "framework": "",
        "test_result": "",
        "retry_count": 0,
        "next_agent": "",
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

    # The workspace is a scratch output area: start every run empty so
    # files from previous runs cannot pollute this one.
    removed = clear_workspace()
    print(f"[MAIN] Cleared workspace for the new run ({removed} item(s) removed).")

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
