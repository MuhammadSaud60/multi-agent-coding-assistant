import re
import subprocess
import sys

from app.config import get_workspace_root
from app.models.state import AgentState

PROJECT_DIR = get_workspace_root()


# Operations that must never be executed from generated code inside the
# workspace: they delete files, and the tester runs generated code with
# the user's permissions. Checked in order; the first match on a line
# wins so the reported label is the most specific one.
_DESTRUCTIVE_PATTERNS = [
    (r"\bos\.remove\s*\(", "os.remove()"),
    (r"\bos\.unlink\s*\(", "os.unlink()"),
    (r"\bos\.rmdir\s*\(", "os.rmdir()"),
    (r"\bos\.removedirs\s*\(", "os.removedirs()"),
    (r"\bshutil\.rmtree\s*\(", "shutil.rmtree()"),
    (r"\.unlink\s*\(", "Path.unlink()"),
    (r"\.rmdir\s*\(", "Path.rmdir()"),
    (
        r"\b(?:os\.system|subprocess\.(?:run|call|Popen|check_output|check_call))"
        r"\s*\([^)]*?[\"'][^\"']*\b(?:rm|del|rmdir)\b",
        "shell delete command",
    ),
    (
        r"\brun_command\s*\(\s*\[\s*[\"'](?:rm|del|rmdir)[\"']",
        "shell delete command via run_command",
    ),
]

_DESTRUCTIVE_REGEXES = [
    (re.compile(pattern), label) for pattern, label in _DESTRUCTIVE_PATTERNS
]

# href/src references inside an HTML file.
_ASSET_REF = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# References that are not files in the workspace.
_EXTERNAL_REF_PREFIXES = ("http://", "https://", "//", "#", "data:", "mailto:", "tel:")


def _result(text: str) -> dict:
    return {
        "test_result": text,
        "final_response": text,
    }


# Directories that never hold project source worth running: caches,
# VCS metadata and virtual environments.
_SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
}

# File basenames that conventionally mark a runnable Python entrypoint.
_PYTHON_ENTRYPOINT_NAMES = {"main.py", "app.py", "index.py", "run.py", "__main__.py"}


def _scan_workspace() -> list[str]:
    """List the project files currently in the workspace.

    Used by run/test-only requests, where no developer produced a file
    list: the tester works from what is actually on disk. Returns
    workspace-relative paths with forward slashes, sorted.
    """

    if not PROJECT_DIR.is_dir():
        return []

    found = []

    for path in PROJECT_DIR.rglob("*"):

        if not path.is_file():
            continue

        parts = path.relative_to(PROJECT_DIR).parts

        if any(
            part in _SKIP_DIR_NAMES or part.startswith(".")
            for part in parts[:-1]
        ):
            continue

        found.append("/".join(parts))

    return sorted(found)


def _pick_workspace_entrypoint(files: list[str]) -> str | None:
    """Pick the entrypoint of an existing workspace project.

    Mirrors the developer's rules: an HTML file means a web project
    (index.html preferred), otherwise a single conventional Python
    entrypoint (or a single Python file at all) runs. Returns None
    when there is no clear entrypoint.
    """

    html_files = [
        path for path in files if path.lower().endswith((".html", ".htm"))
    ]

    if html_files:
        for candidate in html_files:
            if candidate.rsplit("/", 1)[-1] == "index.html":
                return candidate
        return html_files[0]

    python_files = [path for path in files if path.lower().endswith(".py")]

    conventional = [
        path
        for path in python_files
        if path.rsplit("/", 1)[-1] in _PYTHON_ENTRYPOINT_NAMES
    ]

    if len(conventional) == 1:
        return conventional[0]

    if not conventional and len(python_files) == 1:
        return python_files[0]

    return None


def scan_workspace_files() -> list[str]:
    """Public wrapper: the project files currently in the workspace.

    Shared with the answerer and the GUI preview, which need the same
    view of the existing project as a run/test-only test.
    """

    return _scan_workspace()


def pick_workspace_entrypoint(files: list[str]) -> str | None:
    """Public wrapper: the entrypoint of an existing workspace project."""

    return _pick_workspace_entrypoint(files)


def _scan_for_destructive_ops(files: list[str]) -> list[str]:
    """Return one 'file: pattern' string per destructive operation found."""

    findings = []

    for filename in files:

        path = PROJECT_DIR / filename

        if not path.is_file():
            continue

        text = path.read_text(encoding="utf-8", errors="replace")

        for line in text.splitlines():
            for regex, label in _DESTRUCTIVE_REGEXES:
                if regex.search(line):
                    findings.append(f"{filename}: {label}")
                    break

    return findings


def _validate_html_entrypoint(entrypoint: str) -> dict:
    """Validate (never execute) a web project.

    The entrypoint HTML file must exist, contain basic HTML structure,
    and every local .css/.js asset it references must exist next to it.
    """

    entrypoint_path = PROJECT_DIR / entrypoint

    html = entrypoint_path.read_text(encoding="utf-8", errors="replace")

    if not re.search(r"<html[\s>]", html, re.IGNORECASE):
        return _result(
            "STATUS: FAILED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            "REASON: The file does not contain basic HTML structure "
            "(no <html> tag found)."
        )

    missing = []

    for ref in _ASSET_REF.findall(html):

        ref = ref.strip()

        if not ref or ref.startswith(_EXTERNAL_REF_PREFIXES):
            continue

        # Only local stylesheet/script assets are checked.
        ref_path = ref.split("?", 1)[0].split("#", 1)[0]

        if not ref_path.lower().endswith((".css", ".js")):
            continue

        asset_path = (entrypoint_path.parent / ref_path).resolve()

        try:
            asset_path.relative_to(PROJECT_DIR)
        except ValueError:
            missing.append(f"{ref} (points outside the workspace)")
            continue

        if not asset_path.is_file():
            missing.append(ref)

    if missing:
        return _result(
            "STATUS: FAILED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            "REASON: The HTML references local assets that do not exist: "
            + ", ".join(missing)
        )

    print("[TESTER] Web project validated successfully.")

    return _result(
        "STATUS: PASSED\n"
        f"ENTRYPOINT: {entrypoint}\n"
        "MODE: Validated, not executed. Web projects are checked, not "
        "run with Python.\n"
        "CHECKS: The HTML file exists, contains an <html> tag, and "
        "every local .css/.js asset it references exists in the workspace."
    )


def _run_python_entrypoint(files: list[str], entrypoint: str) -> dict:
    """Syntax-check every Python file, then execute the entrypoint."""

    python_files = [
        file for file in files if file.endswith(".py")
    ]

    print(
        f"[TESTER] Checking {len(python_files)} Python files..."
    )

    for filename in python_files:

        file_path = PROJECT_DIR / filename

        result = subprocess.run(
            [
                sys.executable,
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

            return _result(
                "STATUS: FAILED\n"
                f"FILE: {filename}\n"
                f"{result.stderr}"
            )

    print("[TESTER] Syntax checks passed.")
    print(
        f"[TESTER] Running entrypoint: {entrypoint}"
    )

    try:

        result = subprocess.run(
            [
                sys.executable,
                entrypoint,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=PROJECT_DIR,
        )

    except subprocess.TimeoutExpired:

        return _result(
            "STATUS: FAILED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            "REASON: Entrypoint timed out after 15 seconds."
        )

    if result.returncode != 0:

        print("[TESTER] Application failed.")

        return _result(
            "STATUS: FAILED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            f"{result.stderr}"
        )

    print("[TESTER] Application executed successfully.")

    return _result(
        "STATUS: PASSED\n"
        f"ENTRYPOINT: {entrypoint}\n"
        f"OUTPUT:\n{result.stdout}"
    )


def _run_tester(state: AgentState):

    print("\n[TESTER] Starting project validation...")

    files = state.get("files_created", [])
    entrypoint = state.get("entrypoint", "")

    scanned = False

    if not files:
        # No developer ran (a run/test-only request): test the project
        # that already sits in the workspace instead of failing.
        files = _scan_workspace()
        scanned = True

        if not files:
            print("[TESTER] The workspace is empty.")

            return _result(
                "STATUS: FAILED\n"
                "REASON: There is no project in the workspace to run: "
                "the workspace is empty. Build something first, then "
                "ask to run it."
            )

        entrypoint = _pick_workspace_entrypoint(files) or ""

        print(
            f"[TESTER] Found {len(files)} existing file(s) in the "
            f"workspace; entrypoint: {entrypoint or '(none)'}"
        )

        if not entrypoint:
            return _result(
                "STATUS: FAILED\n"
                "REASON: Found an existing project in the workspace "
                "but could not tell which file starts it, so nothing "
                "was run.\n"
                "FILES FOUND:\n"
                + "\n".join(f"- {name}" for name in files)
            )

    if not entrypoint:
        return _result(
            "STATUS: FAILED\n"
            "REASON: No entrypoint was provided."
        )

    entrypoint_path = (PROJECT_DIR / entrypoint).resolve()

    try:
        entrypoint_path.relative_to(PROJECT_DIR)
    except ValueError:
        return _result(
            "STATUS: FAILED\n"
            f"REASON: Entrypoint is outside the workspace: {entrypoint}"
        )

    if not entrypoint_path.exists():

        return _result(
            "STATUS: FAILED\n"
            f"REASON: Entrypoint not found: {entrypoint}"
        )

    # -----------------------------------------
    # Safety gate: never execute destructive code
    # -----------------------------------------

    findings = _scan_for_destructive_ops(files)

    if findings:

        print(
            "[TESTER] BLOCKED: destructive operation(s) found: "
            + "; ".join(findings)
        )

        return _result(
            "BLOCKED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            "REASON: Refused to run the generated code because it "
            "contains destructive file-deletion operations. Nothing "
            "was executed and nothing was deleted.\n"
            "FOUND:\n"
            + "\n".join(f"- {finding}" for finding in findings)
        )

    # -----------------------------------------
    # Branch on the entrypoint type
    # -----------------------------------------

    suffix = entrypoint_path.suffix.lower()

    if suffix == ".py":
        result = _run_python_entrypoint(files, entrypoint)
    elif suffix in (".html", ".htm"):
        result = _validate_html_entrypoint(entrypoint)
    else:
        result = _result(
            "STATUS: FAILED\n"
            f"ENTRYPOINT: {entrypoint}\n"
            f"REASON: Projects with a '{suffix}' entrypoint cannot be "
            "tested yet. The tester can run Python (.py) entrypoints and "
            "validate HTML (.html) entrypoints; this project was not run."
        )

    if scanned:
        # A workspace scan found these files; surface them so the CLI
        # and GUI can show what was actually run.
        result = {
            **result,
            "files_created": files,
            "entrypoint": entrypoint,
        }

    return result


def tester_agent(state: AgentState):
    """Tester node: runs the checks and surfaces any entrypoint fix.

    When the developer had to correct the model's entrypoint, the
    correction line is appended to the result text so the user can see
    what was changed and why. It goes at the END of the text because
    the graph router (and the GUI) read the result's first line.
    """

    result = _run_tester(state)

    note = state.get("entrypoint_note", "")

    if note and result.get("test_result"):
        text = result["test_result"] + f"\nNOTE: {note}"
        result = {
            "test_result": text,
            "final_response": text,
        }

    return result
