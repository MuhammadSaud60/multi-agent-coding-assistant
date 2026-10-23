from langchain_core.tools import tool

from app import config
from app.codeclean import strip_comments
from app.config import get_llm
from app.memory import format_memory_context
from app.models.state import AgentState
from app.tools.command_tools import run_command
from app.tools.project_tools import validate_python_filename
from app.tools.workspace_tools import (
    create_file as _create_file,
    delete_file,
    list_files,
    load_existing_files,
    read_file,
    update_file as _update_file,
)

llm = get_llm("debugger")


# The debugger writes code too, and generated code carries no
# comments: its create/update tools strip comments deterministically
# before writing, exactly like the developer's write path.
@tool
def create_file(filename: str, content: str) -> str:
    """Create or overwrite a file in the project workspace."""

    return _create_file.invoke(
        {"filename": filename, "content": strip_comments(filename, content)}
    )


@tool
def update_file(filename: str, content: str) -> str:
    """Update an existing file in the project workspace."""

    return _update_file.invoke(
        {"filename": filename, "content": strip_comments(filename, content)}
    )


tools = [
    list_files,
    read_file,
    create_file,
    update_file,
    delete_file,
    run_command,
]

_debug_agent = None


def _get_debug_agent():
    """Build the ReAct debugging agent lazily on first use.

    Building it at import time would force tool binding even for runs that
    never fail a test (and for models that cannot bind tools, such as the
    offline fake provider).
    """
    global _debug_agent

    if _debug_agent is None:
        from langchain.agents import create_agent

        _debug_agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt="""
You are an expert software debugging agent.

Your job is to fix actual files in the project.

Rules:
1. Inspect the relevant file using read_file.
2. Identify the real cause of the error.
3. Fix the actual file using update_file.
4. Do not only explain the solution.
5. Do not invent filenames.
6. Preserve working code and change only what is necessary.
7. Write code without any comments.
""",
        )

    return _debug_agent


def _preservation_correction(state: AgentState, complaint: str) -> dict:
    """The one corrective pass after a preservation-guard failure.

    The complaint is precise — it names the content the edit removed
    — so the fix is a single structured regeneration of the affected
    files, not free-form debugging: the model gets the complaint,
    the user's instruction, the project memory and the files as they
    now stand, and returns the corrected files, which are merged
    over the project exactly like a developer edit. The complaint is
    always cleared and the pass always counts, so the run proceeds
    to the tester afterwards whatever happens here — no loops.
    """

    from app.agents.developer import DeveloperOutput, _resolve_entrypoint

    print("\n[DEBUGGER] Corrective pass: restoring removed content...")

    update: dict = {
        "retry_count": state.get("retry_count", 0) + 1,
        "preservation_complaint": "",
    }

    try:
        current_files = load_existing_files()
        current_paths = [item.get("path", "") for item in current_files]

        files_block = "\n\n".join(
            f"----- {item.get('path', '')} -----\n{item.get('content', '')}"
            for item in current_files
        )

        memory_block = format_memory_context(
            state.get("project_memory"), state.get("user_request", "")
        )

        prompt = f"""
    You are fixing an edit that damaged an existing project.

    {complaint}

    {memory_block}

    Current project files (path, then full content):

    {files_block}

    Return the COMPLETE corrected content of each file you change so
    that the removed content is restored exactly as it was AND the
    change the user asked for stays in place. Keep the current
    design (colours, fonts, layout). Files you do not return stay
    exactly as they are. Write code without any comments.
    """

        # Looked up on the module at call time (not bound at import)
        # so the configured provider — and test doubles — apply.
        structured_llm = config.get_structured_llm(DeveloperOutput)
        result = structured_llm.invoke(prompt)

        written = []

        for file in result.files:
            if validate_python_filename(file.path):
                print(
                    f"[DEBUGGER] Skipping unsafe filename: {file.path}"
                )
                continue

            print(f"[DEBUGGER] Correcting file: {file.path}")

            outcome = _create_file.invoke(
                {
                    "filename": file.path,
                    "content": strip_comments(file.path, file.content),
                }
            )

            print(f"[DEBUGGER] {outcome}")
            written.append(file.path)

        deleted = []

        for path in result.files_to_delete:
            if path in written:
                continue
            try:
                outcome = delete_file.invoke({"filename": path})
            except ValueError as exc:
                print(f"[DEBUGGER] Skipping delete of {path}: {exc}")
                continue
            print(f"[DEBUGGER] {outcome}")
            if outcome.startswith("Deleted:"):
                deleted.append(path)

        files_created = [
            path
            for path in current_paths
            if path not in deleted and path not in written
        ] + written

        entrypoint, note = _resolve_entrypoint(
            result.entrypoint, files_created
        )

        update["files_created"] = files_created

        if entrypoint is not None:
            update["entrypoint"] = entrypoint
            if note:
                update["entrypoint_note"] = note
        # An unresolvable entrypoint keeps the run's existing one;
        # the tester judges the corrected files as they stand.

    except Exception as exc:
        # A failed correction must not kill the run or loop the
        # guard: the complaint is already cleared above, and the
        # tester reports on the files as they stand.
        print(
            f"[DEBUGGER] Corrective pass failed: "
            f"{type(exc).__name__}: {exc}"
        )

    print("[DEBUGGER] Corrective pass finished.")

    return update


def debugger_agent(state: AgentState):

    # A preservation complaint is not a test failure: it is the
    # guard's precise statement of what an edit wrongly removed.
    # It gets its own single corrective pass.
    complaint = state.get("preservation_complaint", "")

    if complaint:
        return _preservation_correction(state, complaint)

    print("\n[DEBUGGER] Starting debugging...")

    error = state.get("test_result", "")
    files = state.get("files_created", [])

    print(f"[DEBUGGER] Files: {files}")
    print(f"[DEBUGGER] Error:\n{error}")

    existing_block = ""

    if state.get("edit_mode") and state.get("existing_files"):
        # A follow-up edit run: the debugger sees the project as it
        # stood when the run started, so a fix builds on the user's
        # existing work instead of guessing at it.
        chunks = [
            f"----- {item.get('path', '')} -----\n{item.get('content', '')}"
            for item in state["existing_files"]
        ]
        existing_block = (
            "\nThis run edits an existing project. Its files as the "
            "run started were:\n\n" + "\n\n".join(chunks) + "\n"
        )

    prompt = f"""
The project contains these files:

{files}
{existing_block}
The tester reported this result:

{error}

Find the file responsible for the failure.

Use read_file to inspect it.
Then use update_file to fix it.
Write code without any comments.

After fixing the file, briefly report what you changed.
"""

    response = _get_debug_agent().invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        }
    )

    print("[DEBUGGER] Finished debugging.")

    return {
        "retry_count": state.get("retry_count", 0) + 1,
    }
