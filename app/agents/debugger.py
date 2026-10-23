from app.config import get_llm
from app.models.state import AgentState
from app.tools.command_tools import run_command
from app.tools.workspace_tools import (
    create_file,
    delete_file,
    list_files,
    read_file,
    update_file,
)

llm = get_llm("debugger")

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
""",
        )

    return _debug_agent


def debugger_agent(state: AgentState):

    print("\n[DEBUGGER] Starting debugging...")

    error = state.get("test_result", "")
    files = state.get("files_created", [])

    print(f"[DEBUGGER] Files: {files}")
    print(f"[DEBUGGER] Error:\n{error}")

    prompt = f"""
The project contains these files:

{files}

The tester reported this result:

{error}

Find the file responsible for the failure.

Use read_file to inspect it.
Then use update_file to fix it.

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
