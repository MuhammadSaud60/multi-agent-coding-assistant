from langchain_core.tools import tool
from pathlib import Path


PROJECT_DIR = Path("generated_project")


@tool
def create_file(filename: str, content: str):
    """
    Create a new file and automatically create folders.
    """

    PROJECT_DIR.mkdir(
        exist_ok=True
    )

    file_path = PROJECT_DIR / filename


    # Create missing folders
    file_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    file_path.write_text(
        content,
        encoding="utf-8"
    )


    return f"Created {file_path}"



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