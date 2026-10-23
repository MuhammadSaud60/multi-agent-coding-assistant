from pydantic import BaseModel, Field, field_validator

from app.codeclean import strip_comments
from app.config import get_structured_llm
from app.memory import format_memory_context
from app.models.state import AgentState
from app.text_utils import to_text
from app.tools.project_tools import validate_python_filename
from app.tools.workspace_tools import create_file, delete_file


def _coerce_text(value):
    """Model payloads are not always strings: coerce before validation.

    Small local models regularly return a file's content as a list of
    lines (or content blocks) instead of one string. Without this,
    pydantic rejects the whole payload and the run ends FAILED even
    though the model produced usable code.
    """

    if isinstance(value, list) and value and all(
        isinstance(item, str) for item in value
    ):
        # A list of lines: put the line breaks back.
        return "\n".join(value)

    return to_text(value)


class GeneratedFile(BaseModel):
    path: str = Field(
        description="Relative path of the file to create"
    )

    content: str = Field(
        description="Complete contents of the file"
    )

    @field_validator("path", mode="before")
    @classmethod
    def _path_as_text(cls, value):
        return _coerce_text(value).strip()

    @field_validator("content", mode="before")
    @classmethod
    def _content_as_text(cls, value):
        return _coerce_text(value)


class DeveloperOutput(BaseModel):
    files: list[GeneratedFile] = Field(
        description="All files required to complete the task"
    )

    entrypoint: str = Field(
        description=(
            "The file the tester should check, matching the project type. "
            "For a Python project this is the runnable script, e.g. main.py "
            "or src/main.py. For a website (HTML/CSS/JS) this is the HTML "
            "page, e.g. index.html, never a Python file."
        )
    )

    files_to_delete: list[str] = Field(
        default=[],
        description=(
            "Existing project files to remove (edit mode only). Leave "
            "empty unless the requested change genuinely removes a file."
        ),
    )

    @field_validator("entrypoint", mode="before")
    @classmethod
    def _entrypoint_as_text(cls, value):
        return _coerce_text(value).strip()

    @field_validator("files_to_delete", mode="before")
    @classmethod
    def _files_to_delete_as_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [
                text
                for text in (_coerce_text(item).strip() for item in value)
                if text
            ]
        return []


structured_llm = get_structured_llm(DeveloperOutput)


def _resolve_entrypoint(
    entrypoint: str, files_created: list[str]
) -> tuple[str | None, str]:
    """Pick the entrypoint the tester should use.

    Small local models regularly name the wrong entrypoint (a main.py
    for a website, or a file that was never generated). When the right
    entrypoint is unambiguous it is corrected instead of failing the
    whole run.

    Returns (entrypoint, note) on success, where note is "" when the
    model's entrypoint was already right and a human-readable
    "Entrypoint corrected: ..." line otherwise. Returns (None, reason)
    when no entrypoint can be determined.
    """

    html_files = [
        path
        for path in files_created
        if path.lower().endswith((".html", ".htm"))
    ]
    python_files = [
        path for path in files_created if path.lower().endswith(".py")
    ]

    if html_files:
        # Web project: the entrypoint is the HTML page. index.html is
        # preferred; otherwise the first HTML file is the only sensible
        # candidate.
        if entrypoint in html_files:
            return entrypoint, ""

        corrected = (
            "index.html" if "index.html" in html_files else html_files[0]
        )

        return corrected, (
            f"Entrypoint corrected: {entrypoint or '(none)'} -> "
            f"{corrected} (web project)"
        )

    if entrypoint in files_created:
        return entrypoint, ""

    if len(python_files) == 1:
        corrected = python_files[0]

        return corrected, (
            f"Entrypoint corrected: {entrypoint or '(none)'} -> "
            f"{corrected} (the named entrypoint was not generated)"
        )

    if not files_created:
        return None, "the developer generated no files at all"

    if not python_files:
        return None, (
            f"the entrypoint '{entrypoint}' was not generated and none "
            "of the generated files can be run or validated as an "
            "entrypoint"
        )

    return None, (
        f"the entrypoint '{entrypoint}' was not generated and several "
        f"Python files could be the entrypoint {python_files}, so the "
        "right one cannot be picked automatically"
    )


def _unusable_payload(reason: str, files_created: list[str]) -> dict:
    """Terminal state update for a developer payload that cannot be used.

    The graph routes straight to END when the developer returns a
    test_result, so the run finishes with this honest FAILED message
    instead of an exception traceback.
    """

    failure = (
        "STATUS: FAILED\n"
        "REASON: The developer returned an unusable payload, so there "
        f"is nothing to test: {reason}. No code was executed."
    )

    print(f"[DEVELOPER] Unusable payload: {reason}")

    return {
        "files_created": files_created,
        "entrypoint": "",
        "entrypoint_note": "",
        "test_result": failure,
        "final_response": failure,
    }


def _existing_files_block(existing_files: list) -> str:
    """Render the current project files for an edit-mode prompt."""

    chunks = []

    for item in existing_files:
        chunks.append(
            f"----- {item.get('path', '')} -----\n{item.get('content', '')}"
        )

    return "\n\n".join(chunks)


def _developer_agent_impl(state: AgentState):

    print("\n[DEVELOPER] Generating implementation...")

    request = state["user_request"]
    plan = state.get("plan", "")
    edit_mode = bool(state.get("edit_mode"))
    existing_files = state.get("existing_files", []) if edit_mode else []
    existing_paths = [item.get("path", "") for item in existing_files]

    shared_rules = """
    Rules:
    - Return complete working file contents.
    - Write code without any comments. No line comments, no block
      comments, no docstring-style explanations inside the code.
    - Write every file properly formatted: real newlines and
      indentation, exactly as a human would save it. NEVER compress
      code onto a single line. This matters most for Python, where a
      function definition, its body and the if __name__ == "__main__":
      guard each belong on their own lines; HTML, CSS and JavaScript
      must likewise be spread over readable lines.
    - Use relative file paths.
    - Do not use markdown code fences.
    - Do not only describe the code.
    - Actually provide the complete contents of every required file.
    - Choose the correct project entrypoint dynamically.
    - The entrypoint must match the project type:
      - Python project: the entrypoint is the runnable Python file
        (for example main.py).
      - Website (HTML/CSS/JS): the entrypoint is the HTML page
        (for example index.html). Do NOT invent a Python file such as
        main.py just to act as an entrypoint for a web project.
    - Do not invent an entrypoint that does not exist.
    """

    if edit_mode:
        memory_block = format_memory_context(
            state.get("project_memory"), request
        )

        prompt = f"""
    You are a senior software developer.

    This project ALREADY EXISTS in the workspace. The user is asking
    for a CHANGE to it, not a new project. Apply only the requested
    change and keep everything else exactly as it is.

    User request:
    {request}

    Development plan:
    {plan}

    {memory_block}

    Current project files (path, then full content):

    {_existing_files_block(existing_files)}

    DO NOT:
    - Do not remove or rewrite existing text, headings or elements
      unless the instruction explicitly asks to remove them.
    - Do not change the layout, colour palette, fonts or background
      unless the instruction asks for that change.
    - Do not rename existing CSS classes without updating every
      file that uses them.
    - Do not redesign. Make the SMALLEST change that satisfies the
      instruction, based on the current file contents above: edit
      those files, do not invent a new version of the project.

    How to respond:
    - Return the COMPLETE updated content of each file you change,
      plus the complete content of any new file you add.
    - Files you do not return stay exactly as they are; do not
      return unchanged files.
    - If the change genuinely removes a file, list its path in
      files_to_delete; otherwise leave files_to_delete empty.
    - The entrypoint must be one of the project's files after your
      change (an existing file or one you return).
    {shared_rules}
    """
    else:
        prompt = f"""
    You are a senior software developer.

    User request:
    {request}

    Development plan:
    {plan}

    Create every file required to complete the task.
    {shared_rules}
    - The entrypoint must be one of the files you return.
    """

    result = structured_llm.invoke(prompt)

    written = []

    for file in result.files:

        validation_error = validate_python_filename(file.path)

        if validation_error:
            # A bad filename from the model must not kill the run:
            # skip that one file and carry on with the rest.
            print(
                f"[DEVELOPER] Skipping unsafe filename: {file.path}"
            )

            print(
                f"[DEVELOPER] {validation_error}"
            )

            continue

        print(
            f"[DEVELOPER] Creating file: {file.path}"
        )

        # Generated code carries no comments: strip deterministically
        # here, whatever the model produced (see app/codeclean.py).
        create_result = create_file.invoke(
            {
                "filename": file.path,
                "content": strip_comments(file.path, file.content),
            }
        )

        print(
            f"[DEVELOPER] {create_result}"
        )

        written.append(file.path)

    if edit_mode:
        # Merge, never replace: the returned files are written over
        # the existing project, only files_to_delete are removed,
        # and every other existing file stays byte-identical.
        deleted = []

        for path in result.files_to_delete:
            if path in written:
                continue
            try:
                outcome = delete_file.invoke({"filename": path})
            except ValueError as exc:
                print(f"[DEVELOPER] Skipping delete of {path}: {exc}")
                continue
            print(f"[DEVELOPER] {outcome}")
            if outcome.startswith("Deleted:"):
                deleted.append(path)

        files_created = [
            path
            for path in existing_paths
            if path not in deleted and path not in written
        ] + written
    else:
        files_created = written

    # Payload validation: the entrypoint must be one of the project
    # files, and it must match the project type. Small models get
    # this wrong often (a main.py entrypoint for a website, or a file
    # that was never generated), so an unambiguous mismatch is
    # corrected here instead of raising and killing the run.
    entrypoint, note_or_reason = _resolve_entrypoint(
        result.entrypoint, files_created
    )

    if entrypoint is None and edit_mode:
        # The change may not have touched the entrypoint at all; the
        # existing project's own entrypoint still applies.
        from app.agents.tester import pick_workspace_entrypoint

        candidate = pick_workspace_entrypoint(files_created)

        if candidate:
            entrypoint = candidate
            note_or_reason = (
                f"Entrypoint kept from the existing project: {candidate}"
            )

    if entrypoint is None:
        return _unusable_payload(note_or_reason, files_created)

    if note_or_reason:
        print(f"[DEVELOPER] {note_or_reason}")

    print(
        f"[DEVELOPER] Entrypoint: {entrypoint}"
    )

    return {
        "files_created": files_created,
        "entrypoint": entrypoint,
        "entrypoint_note": note_or_reason,
    }


def developer_agent(state: AgentState):
    """Developer node: never lets a bad model payload crash the graph.

    A malformed payload (missing fields, an unusable entrypoint, an
    unsafe filename) used to propagate out of the graph invocation as
    a bare ValueError. Now every such case ends the run gracefully
    with an honest FAILED result.
    """

    try:
        return _developer_agent_impl(state)
    except Exception as exc:
        print(
            f"[DEVELOPER] Payload handling failed: "
            f"{type(exc).__name__}: {exc}"
        )

        return _unusable_payload(
            f"the model's response could not be used "
            f"({type(exc).__name__}: {exc})",
            list(state.get("files_created", [])),
        )
