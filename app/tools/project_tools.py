from pathlib import Path
import sys


PROJECT_DIR = Path("generated_project")


def validate_python_filename(filename: str) -> str | None:
    """
    Prevent Python files from shadowing standard-library modules.
    """

    if not filename.endswith(".py"):
        return None

    module_name = Path(filename).stem

    if module_name == "__init__":
        return None

    # Python 3.10+ provides the standard library module names.
    stdlib_modules = sys.stdlib_module_names

    if module_name in stdlib_modules:
        return (
            f"Unsafe filename '{filename}'. "
            f"It shadows the Python standard-library module "
            f"'{module_name}'. Choose a different filename."
        )

    return None