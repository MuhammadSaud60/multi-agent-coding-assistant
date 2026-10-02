from langchain_core.tools import tool
from pathlib import Path


PROJECT_DIR = Path("generated_project")


@tool
def create_file(filename: str, content: str):

    """
        Create a new file when needed
    """

    PROJECT_DIR.mkdir(
        exist_ok=True
    )

    file_path = PROJECT_DIR / filename

    file_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    file_path.write_text(
        content,
        encoding="utf-8"
    )

    return {
        "status": "created",
        "file": filename
    }


@tool
def read_file(filename: str):
    """
    Read an existing file.
    """

    file_path = PROJECT_DIR / filename

    if not file_path.exists():
        return "File does not exist"
    

    return file_path.read_text(
        encoding="utf-8"
    )


@tool
def update_file(filename: str, content: str):
    """
    Update an existing file with new content.
    """

    file_path = PROJECT_DIR / filename

    if not file_path.exists():
        return "File does not exist"


    file_path.write_text(
        content,
        encoding="utf-8"
    )


    return f"Updated {filename}"