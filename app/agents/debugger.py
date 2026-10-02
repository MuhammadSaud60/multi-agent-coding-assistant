from langchain_ollama import ChatOllama
from langchain.agents import create_agent

from app.models.state import AgentState
from app.tools.file_tools import (
    read_file,
    update_file,
)


llm = ChatOllama(
    model="llama3.2:3b",
    temperature=0,
)


tools = [
    read_file,
    update_file,
]


debug_agent = create_agent(
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

    response = debug_agent.invoke(
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
        "code": response["messages"][-1].content,
        "retry_count": state.get("retry_count", 0) + 1,
    }