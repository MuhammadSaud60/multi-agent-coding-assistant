from pathlib import Path
import subprocess

from app.models.state import AgentState


PROJECT_DIR = Path("generated_project")


def tester_agent(state: AgentState):

    print("\n[TESTER] Starting project validation...")

    files = state.get("files_created", [])
    entrypoint = state.get("entrypoint", "")

    if not files:
        print("[TESTER] No files were created.")

        return {
            "test_result": (
                "STATUS: FAILED\n"
                "REASON: No project files were created."
            )
        }

    # -----------------------------------------
    # 1. Syntax check every Python file
    # -----------------------------------------

    python_files = [
        file
        for file in files
        if file.endswith(".py")
    ]

    print(
        f"[TESTER] Checking {len(python_files)} Python files..."
    )

    for filename in python_files:

        file_path = PROJECT_DIR / filename

        result = subprocess.run(
            [
                "python",
                "-m",
                "py_compile",
                str(file_path),
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:

            print(
                f"[TESTER] Syntax error in {filename}"
            )

            return {
                "test_result": (
                    "STATUS: FAILED\n"
                    f"FILE: {filename}\n"
                    f"{result.stderr}"
                )
            }

    print("[TESTER] Syntax checks passed.")

    # -----------------------------------------
    # 2. Validate entrypoint exists
    # -----------------------------------------

    if not entrypoint:

        return {
            "test_result": (
                "STATUS: FAILED\n"
                "REASON: No entrypoint was provided."
            )
        }

    entrypoint_path = PROJECT_DIR / entrypoint

    if not entrypoint_path.exists():

        return {
            "test_result": (
                "STATUS: FAILED\n"
                f"REASON: Entrypoint not found: {entrypoint}"
            )
        }

    # -----------------------------------------
    # 3. Run ONLY the entrypoint
    # -----------------------------------------

    print(
        f"[TESTER] Running entrypoint: {entrypoint}"
    )

    result = subprocess.run(
    [
        "python",
        entrypoint,
    ],
    capture_output=True,
    text=True,
    timeout=15,
    cwd=PROJECT_DIR,
)

    if result.returncode != 0:

        print("[TESTER] Application failed.")

        return {
            "test_result": (
                "STATUS: FAILED\n"
                f"ENTRYPOINT: {entrypoint}\n"
                f"{result.stderr}"
            )
        }

    print("[TESTER] Application executed successfully.")

    return {
        "test_result": (
            "STATUS: PASSED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            f"OUTPUT:\n{result.stdout}"
        )
    }