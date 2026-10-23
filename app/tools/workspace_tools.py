"""The single file API for the generated-project workspace.

All file access by agents goes through these tools. Paths are resolved
against the workspace root (see app.config.get_workspace_root, env var
MACA_WORKSPACE) and can never escape it. Python filenames that would shadow
a standard-library module are rejected on create.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from langchain_core.tools import tool

from app.config import get_workspace_root
from app.tools.project_tools import validate_python_filename

PROJECT_DIR = get_workspace_root()


class ClearResult(NamedTuple):
    """Outcome of a run-start workspace clear."""

    moved: int
    backup_dir: Path | None


def get_backup_root() -> Path:
    """Where previous projects are archived: a sibling of the workspace.

    For the default workspace this is
    <project root>/generated_project_backups. It deliberately lives
    OUTSIDE the workspace root, so the file APIs (which only ever look
    inside the workspace) never list backups as project files, and a
    later clear can never delete an earlier backup.
    """

    return PROJECT_DIR.parent / f"{PROJECT_DIR.name}_backups"


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


def clear_workspace() -> ClearResult:
    """
    Empty the workspace root for a new run WITHOUT deleting anything.

    Each new run starts from an empty workspace so files from previous
    runs cannot pollute it, but the previous project's files are the
    user's work: instead of being deleted they are MOVED into a
    timestamped folder under get_backup_root(), outside the workspace,
    so a run can never silently destroy the project before it.

    The same path-safety rule as safe_path applies: a child is only
    moved after resolving it and confirming it still lives inside the
    workspace root, so nothing outside the root can ever be touched.
    A symlink is moved as a link, never followed.

    Returns a ClearResult: how many top-level items were moved, and
    the backup folder they went to (None when the workspace was
    already empty).
    """

    PROJECT_DIR.mkdir(parents=True, exist_ok=True)

    children = list(PROJECT_DIR.iterdir())

    if not children:
        return ClearResult(moved=0, backup_dir=None)

    backup_root = get_backup_root()
    backup_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = backup_root / stamp
    suffix = 2
    while backup_dir.exists():
        backup_dir = backup_root / f"{stamp}-{suffix}"
        suffix += 1
    backup_dir.mkdir(parents=True)

    moved = 0

    for child in children:

        if child.is_symlink():
            # Move the link itself; never follow it outside the root.
            shutil.move(str(child), str(backup_dir / child.name))
            moved += 1
            continue

        resolved = child.resolve()

        try:
            resolved.relative_to(PROJECT_DIR)
        except ValueError:
            print(
                f"[WORKSPACE] Skipping path outside workspace: {child}"
            )
            continue

        shutil.move(str(resolved), str(backup_dir / child.name))

        moved += 1

    if moved == 0:
        # Nothing was safe to move; do not leave an empty backup
        # folder behind as a false promise.
        backup_dir.rmdir()
        return ClearResult(moved=0, backup_dir=None)

    return ClearResult(moved=moved, backup_dir=backup_dir)


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
