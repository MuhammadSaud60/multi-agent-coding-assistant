#!/usr/bin/env bash
# One-click start for Linux, Mac and GitHub Codespaces:
# sets up everything on first run, then opens the GUI.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: creating the Python environment, one moment..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [ ! -f .env ]; then
  echo "No .env file found, creating one from .env.example"
  cp .env.example .env
  echo "Edit .env to pick your model, then run this file again if needed."
fi

python run.py
