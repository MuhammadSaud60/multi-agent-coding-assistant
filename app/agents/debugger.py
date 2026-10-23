from langchain_core.tools import tool

from app.codeclean import strip_comments
from app.config import get_llm
from app.models.state import AgentState
from app.tools.command_tools import run_command
from app.tools.workspace_tools import (
    create_file as _create_file,
    delete_file,
    list_files,
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


def debugger_agent(state: AgentState):

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
