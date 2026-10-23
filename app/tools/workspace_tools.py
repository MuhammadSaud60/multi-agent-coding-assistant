"""The single file API for the generated-project workspace.

All file access by agents goes through these tools. Paths are resolved
against the workspace root (see app.config.get_workspace_root, env var
MACA_WORKSPACE) and can never escape it. Python filenames that would shadow
a standard-library module are rejected on create.
"""

import shutil
from pathlib import Path

from langchain_core.tools import tool

from app.config import get_workspace_root
from app.tools.project_tools import validate_python_filename

PROJECT_DIR = get_workspace_root()


def safe_path(relative_path: str) -> Path:
    """
    Resolve a path while ensuring it stays inside the generated project.
    """

    target = (PROJECT_DIR / relative_path).resolve()

    try:
        target.relative_to(PROJECT_DIR)
    except ValueError:
        raise ValueError(
            f"Path is outside project workspace: {relative_path}"
        )

    return target


def clear_workspace() -> int:
    """
    Remove every file and folder inside the workspace root, keeping the
    root folder itself.

    The workspace is a scratch output area: each new run starts from an
    empty workspace so files from previous runs cannot pollute it. The
    same path-safety rule as safe_path applies: a child is only removed
    after resolving it and confirming it still lives inside the
    workspace root, so nothing outside the root can ever be deleted.

    Returns the number of top-level items removed.
    """

    PROJECT_DIR.mkdir(parents=True, exist_ok=True)

    removed = 0

    for child in PROJECT_DIR.iterdir():

        resolved = child.resolve()

        try:
            resolved.relative_to(PROJECT_DIR)
        except ValueError:
            print(
                f"[WORKSPACE] Skipping path outside workspace: {child}"
            )
            continue

        if child.is_symlink():
            child.unlink()
        elif resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()

        removed += 1

    return removed


@tool
def list_files() -> list[str]:
    """
    List all files inside the project workspace.
    """

    if not PROJECT_DIR.exists():
        return []

    files = []

    for path in PROJECT_DIR.rglob("*"):
        if path.is_file():
            files.append(str(path.relative_to(PROJECT_DIR)))

    return sorted(files)


@tool
def read_file(filename: str) -> str:
    """
    Read a file from the project workspace.
    """

    path = safe_path(filename)

    if not path.exists():
        return f"File not found: {filename}"

    if not path.is_file():
        return f"Not a file: {filename}"

    return path.read_text(encoding="utf-8")


@tool
def create_file(filename: str, content: str) -> str:
    """
    Create or overwrite a file in the project workspace.
    """

    validation_error = validate_python_filename(filename)

    if validation_error:
        raise ValueError(validation_error)

    path = safe_path(filename)

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")

    return f"Created: {filename}"


@tool
def update_file(filename: str, content: str) -> str:
    """
    Update an existing file.
    """

    path = safe_path(filename)

    if not path.exists():
        return f"File not found: {filename}"

    path.write_text(content, encoding="utf-8")

    return f"Updated: {filename}"


@tool
def delete_file(filename: str) -> str:
    """
    Delete a file from the project workspace.
    """

    path = safe_path(filename)

    if not path.exists():
        return f"File not found: {filename}"

    if not path.is_file():
        return f"Not a file: {filename}"

    path.unlink()

    return f"Deleted: {filename}"
