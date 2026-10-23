"""Direct workspace actions: requests performed, not generated.

Some requests are operations on the workspace itself, not projects
to build. The first of those is deletion ("delete the existing
file", "delete notes.txt", "delete all files"). Generating code for
them is wrong twice over: the developer invents a deletion script
with a placeholder filename, and the tester then blocks that script
as destructive, so the run "finishes" without the user's file ever
being touched.

handle_delete_request() performs the operation through the same
safe workspace tools everything else uses and returns a plain
outcome message, or None when the request is not a pure delete
request (the caller then runs the normal agent flow).

Safety posture:
- A named file is deleted exactly, through safe_path(), so a name
  can never escape the workspace.
- Anything broader (all files, or the workspace's only file) is
  NOT hard-deleted: it goes through clear_workspace(), which moves
  the contents into the timestamped backup folder, so the removal
  is reversible and the message names where the files went.
- An ambiguous request (several files, none named, no "all")
  deletes nothing and lists what is present instead.
"""

from pathlib import Path

from app.intent import parse_delete_request
from app.tools.workspace_tools import PROJECT_DIR, clear_workspace, safe_path


def _workspace_files() -> list[str]:
    """All files in the workspace, as workspace-relative paths."""

    if not PROJECT_DIR.exists():
        return []

    return sorted(
        str(path.relative_to(PROJECT_DIR))
        for path in PROJECT_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def _resolve_named_file(filename: str, files: list[str]) -> str | None:
    """Find the workspace-relative path a named file refers to.

    Exact match first, then a case-insensitive path match, then a
    unique basename match ("delete notes.txt" for docs/notes.txt).
    None when there is no unambiguous match.
    """

    if filename in files:
        return filename

    lowered = filename.lower()

    for candidate in files:
        if candidate.lower() == lowered:
            return candidate

    by_name = [
        candidate
        for candidate in files
        if Path(candidate).name.lower() == Path(filename).name.lower()
    ]

    if len(by_name) == 1:
        return by_name[0]

    return None


def _move_all_to_backup(files: list[str]) -> str:
    """Empty the workspace via the backup mechanism and report it."""

    result = clear_workspace()

    if result.backup_dir is None:
        return "The workspace is already empty; nothing was deleted."

    lines = [
        f"Moved {result.moved} item(s) to backup: {result.backup_dir}",
        "Removed from the workspace:",
    ]
    lines.extend(f"- {name}" for name in files)
    lines.append(
        "The files were moved, not destroyed: restore them from the "
        "backup folder if you need them back."
    )

    return "\n".join(lines)


def handle_delete_request(request) -> str | None:
    """Perform a pure delete request; return its outcome message.

    Returns None when the request is not a pure delete request, so
    the caller can fall through to the normal agent flow.
    """

    intent = parse_delete_request(request)

    if intent is None:
        return None

    files = _workspace_files()

    if intent.kind == "named":
        target = _resolve_named_file(intent.filename, files)

        if target is None:
            return (
                f"File not found in the workspace: {intent.filename} "
                "(nothing was deleted)."
            )

        try:
            path = safe_path(target)
        except ValueError:
            return (
                f"That path is outside the workspace: {intent.filename} "
                "(nothing was deleted)."
            )

        if not path.is_file():
            return (
                f"Not a file in the workspace: {intent.filename} "
                "(nothing was deleted)."
            )

        path.unlink()

        return f"Deleted from workspace: {target}"

    if intent.kind == "all":
        if not files:
            return "The workspace is already empty; nothing was deleted."
        return _move_all_to_backup(files)

    # kind == "plain": "delete the existing file" with no name. What
    # that may mean depends on how many files are actually present.
    if not files:
        return "The workspace is already empty; nothing was deleted."

    if len(files) == 1:
        # Unambiguous: there is only one "the existing file".
        return _move_all_to_backup(files)

    lines = [
        "Nothing was deleted: the request does not say which file.",
        f"The workspace contains {len(files)} files:",
    ]
    lines.extend(f"- {name}" for name in files)
    lines.append(
        "Name the file to delete (for example \"delete "
        f"{files[0]}\"), or say \"all\" to remove everything."
    )

    return "\n".join(lines)
