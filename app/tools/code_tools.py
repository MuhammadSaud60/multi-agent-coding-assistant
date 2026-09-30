from langchain_core.tools import tool
from pathlib import Path
import subprocess


PROJECT_DIR = Path("generated_project")


@tool
def run_python_file(filename: str):
    """
    Run a Python file and return output.
    """

    file_path = PROJECT_DIR / filename

    if not file_path.exists():
        return "File does not exist"


    try:

        result = subprocess.run(
            ["python", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10
        )


        if result.returncode == 0:
            return result.stdout

        else:
            return result.stderr


    except Exception as e:
        return str(e)