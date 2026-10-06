from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama

from models.state import AgentState
from tools.workspace_tools import (
    list_files,
    read_file,
    create_file,
    update_file,
    delete_file,
)
from tools.project_tools import validate_python_filename


class GeneratedFile(BaseModel):
    path: str = Field(
        description="Relative path of the file to create"
    )

    content: str = Field(
        description="Complete contents of the file"
    )


class DeveloperOutput(BaseModel):
    files: list[GeneratedFile] = Field(
        description="All files required to complete the task"
    )

    entrypoint: str = Field(
        description=(
            "The main executable file used to test the project. "
            "For example: main.py, app.py, or src/main.py"
        )
    )


llm = ChatOllama(
    model="llama3.2:3b",
    
    temperature=0
)


structured_llm = llm.with_structured_output(
    DeveloperOutput,
    method="json_schema"
)


def developer_agent(state: AgentState):

    print("\n[DEVELOPER] Generating implementation...")

    request = state["user_request"]
    plan = state.get("plan", "")

    prompt = f"""
    You are a senior software developer.

    User request:
    {request}

    Development plan:
    {plan}

    Create every file required to complete the task.

    Rules:
    - Return complete working file contents.
    - Use relative file paths.
    - Do not use markdown code fences.
    - Do not only describe the code.
    - Actually provide the complete contents of every required file.
    - Choose the correct project entrypoint dynamically.
    - The entrypoint must be a file that can be executed to test the project.
    - Do not invent an entrypoint that does not exist.
    """

    result = structured_llm.invoke(prompt)

    files_created = []

    for file in result.files:

        validation_error = validate_python_filename(file.path)

        if validation_error:
            print(
                f"[DEVELOPER] Unsafe filename detected: {file.path}"
            )

            print(
                f"[DEVELOPER] {validation_error}"
            )

            raise ValueError(validation_error)

        print(
            f"[DEVELOPER] Creating file: {file.path}"
        )

        create_result = create_file.invoke(
            {
                "filename": file.path,
                "content": file.content
            }
        )

        print(
            f"[DEVELOPER] {create_result}"
        )

        files_created.append(file.path)

        print(
            f"[DEVELOPER] Entrypoint: {result.entrypoint}"
        )

    return {
        "code": result.model_dump_json(),
        "files_created": files_created,
        "entrypoint": result.entrypoint,
    }