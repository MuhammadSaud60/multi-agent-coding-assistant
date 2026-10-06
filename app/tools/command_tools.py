from pathlib import Path
import subprocess

from langchain_core.tools import tool


PROJECT_DIR = Path("generated_project").resolve()


ALLOWED_COMMANDS = {
    "python",
    "python3",
    "pytest",
    "node",
    "npm",
    "npx",
    "yarn",
    "pnpm",
    "java",
    "javac",
    "mvn",
    "gradle",
    "cargo",
    "rustc",
    "go",
}


@tool
def run_command(
    command: list[str],
    timeout: int = 30
) -> str:
    """
    Run a project command inside the generated project workspace.

    Example:
    ["python", "main.py"]
    ["npm", "test"]
    ["npm", "run", "build"]
    ["pytest"]
    """

    if not command:
        return "ERROR: Empty command."

    executable = command[0].lower()

    if executable not in ALLOWED_COMMANDS:
        return (
            f"ERROR: Command '{command[0]}' "
            "is not allowed."
        )

    print(
        f"[TOOL:run_command] "
        f"{' '.join(command)}"
    )

    try:

        result = subprocess.run(
            command,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )

    except subprocess.TimeoutExpired:

        return (
            "STATUS: FAILED\n"
            "REASON: Command timed out."
        )

    except FileNotFoundError:

        return (
            "STATUS: FAILED\n"
            f"REASON: Executable not found: {command[0]}"
        )

    except Exception as exc:

        return (
            "STATUS: FAILED\n"
            f"REASON: {exc}"
        )

    output = "\n".join(
        part
        for part in [
            result.stdout.strip(),
            result.stderr.strip(),
        ]
        if part
    )

    if result.returncode == 0:

        return (
            "STATUS: PASSED\n"
            f"COMMAND: {' '.join(command)}\n"
            f"OUTPUT:\n{output}"
        )

    return (
        "STATUS: FAILED\n"
        f"COMMAND: {' '.join(command)}\n"
        f"EXIT CODE: {result.returncode}\n"
        f"OUTPUT:\n{output}"
    )