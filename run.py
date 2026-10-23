"""One-command launcher for the multi-agent coding assistant.

Usage (from anywhere, but simplest from the project root):

    python run.py                          -> opens the GUI (http://127.0.0.1:8765)
    python run.py "make a calculator app"  -> runs the agents in the terminal

Model settings live in the .env file in the project root; see .env.example.
"""

import sys
from pathlib import Path

# Make sure the project root is importable even when started elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from app.main import main as cli_main

        raise SystemExit(cli_main(sys.argv[1:]))
    from app.gui import main as gui_main

    gui_main()
