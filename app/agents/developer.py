from pydantic import BaseModel, Field

from app.config import get_structured_llm
from app.models.state import AgentState
from app.tools.project_tools import validate_python_filename
from app.tools.workspace_tools import create_file


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
            "The file the tester should check, matching the project type. "
            "For a Python project this is the runnable script, e.g. main.py "
            "or src/main.py. For a website (HTML/CSS/JS) this is the HTML "
            "page, e.g. index.html, never a Python file."
        )
    )


structured_llm = get_structured_llm(DeveloperOutput)


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
    - The entrypoint must match the project type:
      - Python project: the entrypoint is the runnable Python file
        (for example main.py).
      - Website (HTML/CSS/JS): the entrypoint is the HTML page
        (for example index.html). Do NOT invent a Python file such as
        main.py just to act as an entrypoint for a web project.
    - The entrypoint must be one of the files you return.
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

    # Payload validation: the entrypoint must be one of the files just
    # created, and it must match the project type. In particular, a web
    # project (which contains an HTML file) is never entered through a
    # Python file invented only to satisfy the tester.
    if result.entrypoint not in files_created:
        raise ValueError(
            f"Invalid entrypoint '{result.entrypoint}': it is not one of "
            f"the generated files {files_created}."
        )

    is_web_project = any(
        path.lower().endswith((".html", ".htm")) for path in files_created
    )

    if is_web_project and result.entrypoint.lower().endswith(".py"):
        raise ValueError(
            f"Invalid entrypoint '{result.entrypoint}': this is a web "
            "project (it contains an HTML file), so the entrypoint must "
            "be the HTML page, not a Python file."
        )

    print(
        f"[DEVELOPER] Entrypoint: {result.entrypoint}"
    )

    return {
        "files_created": files_created,
        "entrypoint": result.entrypoint,
    }
