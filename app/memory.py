"""Persistent project memory: the assistant's memory across runs.

Round 9 made follow-up instructions EDIT the existing project, but
the model still rewrote whole files with a fresh design on every
edit: it was never told what the user had asked before, or what the
project currently looks like. This module is the memory that fixes
that. It survives runs AND workspace backup/clear because it lives
BESIDE the workspace (see app.config.get_memory_path), never in it.

The memory is a single JSON document:

    {
      "workspace": "<resolved workspace path this memory belongs to>",
      "original_request": "the first request that started the project",
      "history": [
        {"request": ..., "mode": "fresh" | "edit", "at": <iso time>},
        ...
      ],
      "design": {
        "colors": ["#ff5733", ...],       hex colours in use
        "fonts": ["Poppins", ...],        font-family values in use
        "background": "#f5f5f5" | null,   body background, if known
        "elements": ["heading", ...]      page element kinds present
      },
      "updated_at": <iso time>
    }

Rules:
- A FRESH build resets the memory around the new original request.
- An EDIT appends to the history and refreshes the design tokens.
- Question, delete and run/test-only runs never touch the history:
  they are not build instructions.
- A missing or corrupt file, or one keyed to a different workspace,
  starts fresh. Memory must NEVER crash a run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from app.config import get_memory_path, get_workspace_root
from app.tools.workspace_tools import load_existing_files

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_BODY_BACKGROUND_RE = re.compile(
    r"body\s*\{[^}]*?background(?:-color)?\s*:\s*"
    r"(#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b)",
    re.IGNORECASE | re.DOTALL,
)
_STYLE_BLOCK_RE = re.compile(
    r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL
)
_STYLE_ATTR_RE = re.compile(r"""style\s*=\s*"([^"]*)"|style\s*=\s*'([^']*)'""")
_TAG_RE = re.compile(
    r"<(h[1-6]|p|a|img|button|form|ul|ol|table|header|footer|nav|main"
    r"|section)\b",
    re.IGNORECASE,
)
_CLASS_ATTR_RE = re.compile(r"""class\s*=\s*"([^"]*)"|class\s*=\s*'([^']*)'""")

_TAG_KINDS = {"p": "paragraph"}

# Class names that mark a decorative shape element on a page.
_SHAPE_CLASSES = {
    "shape",
    "circle",
    "square",
    "triangle",
    "rectangle",
    "diamond",
    "star",
    "oval",
    "box",
}


def _empty_memory() -> dict:
    return {
        "workspace": str(get_workspace_root()),
        "original_request": "",
        "history": [],
        "design": {},
        "updated_at": "",
    }


def load_memory() -> dict:
    """The stored memory for THIS workspace, or an empty one.

    The file is keyed by the workspace path it sits beside: a memory
    written for a different workspace (a shared parent folder, a
    moved project) is not this project's memory and is ignored.
    Any problem at all — missing file, bad JSON, wrong shape —
    yields the empty memory rather than an exception.
    """

    try:
        raw = get_memory_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return _empty_memory()

    if not isinstance(data, dict):
        return _empty_memory()

    if data.get("workspace") != str(get_workspace_root()):
        return _empty_memory()

    memory = _empty_memory()
    if isinstance(data.get("original_request"), str):
        memory["original_request"] = data["original_request"]
    if isinstance(data.get("history"), list):
        memory["history"] = [
            entry
            for entry in data["history"]
            if isinstance(entry, dict) and isinstance(entry.get("request"), str)
        ]
    if isinstance(data.get("design"), dict):
        memory["design"] = data["design"]
    if isinstance(data.get("updated_at"), str):
        memory["updated_at"] = data["updated_at"]

    return memory


def save_memory(memory: dict) -> None:
    """Persist the memory. A failed write is reported, never raised."""

    memory["workspace"] = str(get_workspace_root())
    memory["updated_at"] = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    try:
        path = get_memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(memory, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[MEMORY] Could not save project memory: {exc}")


def _css_sources(files: list) -> str:
    """All CSS text of a project: .css files, <style> blocks, style attrs."""

    chunks = []

    for item in files:
        path = item.get("path", "")
        content = item.get("content", "")

        if path.lower().endswith(".css"):
            chunks.append(content)
        elif path.lower().endswith((".html", ".htm")):
            chunks.extend(_STYLE_BLOCK_RE.findall(content))
            for match in _STYLE_ATTR_RE.findall(content):
                chunks.append(match[0] or match[1])

    return "\n".join(chunks)


def extract_design_tokens(files: list) -> dict:
    """The design fingerprint of a project's current files.

    Hex colours and font-family values from every CSS source, the
    body background colour when a body rule names one, and the kinds
    of page elements present in the HTML (headings, paragraphs,
    shapes, ...). Deterministic: the same files always yield the
    same tokens, so a follow-up edit can be told exactly what the
    current design is and be held to it.
    """

    css = _css_sources(files)

    colors = sorted({match.lower() for match in _HEX_RE.findall(css)})

    fonts = []
    for raw in _FONT_FAMILY_RE.findall(css):
        family = raw.split(",", 1)[0].strip().strip("'\"")
        if family and family not in fonts:
            fonts.append(family)

    background = None
    body_match = _BODY_BACKGROUND_RE.search(css)
    if body_match:
        background = body_match.group(1).lower()

    elements = set()

    for item in files:
        if not item.get("path", "").lower().endswith((".html", ".htm")):
            continue

        content = item.get("content", "")

        for tag in _TAG_RE.findall(content):
            tag = tag.lower()
            if tag.startswith("h") and tag[1:].isdigit():
                elements.add("heading")
            else:
                elements.add(_TAG_KINDS.get(tag, tag))

        for match in _CLASS_ATTR_RE.findall(content):
            classes = (match[0] or match[1]).split()
            if any(
                name in _SHAPE_CLASSES or "shape" in name for name in classes
            ):
                elements.add("shape")

    return {
        "colors": colors,
        "fonts": sorted(fonts),
        "background": background,
        "elements": sorted(elements),
    }


def record_run(
    request: str,
    mode: str,
    *,
    success: bool,
    files: list | None = None,
) -> dict:
    """The finish step shared by both entry points (CLI and GUI).

    Called after a build/edit graph run completes. A fresh build
    resets the memory around the new original request; an edit
    appends to the existing memory. Design tokens are refreshed
    from the resulting workspace files after a successful run.
    Returns the memory as stored.
    """

    entry = {
        "request": request,
        "mode": mode,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    if mode == "fresh":
        memory = _empty_memory()
        memory["original_request"] = request
        memory["history"] = [entry]
    else:
        memory = load_memory()
        if not memory["original_request"]:
            # A project that predates memory (or a lost memory file):
            # the earliest request we can still know about is this
            # one, so it becomes the recorded starting point.
            memory["original_request"] = request
        memory["history"] = memory["history"] + [entry]

    if success:
        memory["design"] = extract_design_tokens(
            files if files is not None else load_existing_files()
        )

    save_memory(memory)

    return memory


def format_memory_context(memory: dict | None, current_request: str) -> str:
    """The memory block for planner/developer prompts on edit runs.

    Spells out the full instruction history ("The user first asked:
    X. Then they asked: Y. Now they ask: Z.") and the locked design
    tokens, so an edit is made with the whole conversation and the
    current design in view instead of as a fresh redesign.
    """

    if not memory:
        return ""

    history = memory.get("history") or []
    design = memory.get("design") or {}

    if not history and not design:
        return ""

    lines = [
        "Project memory: this project was built step by step. "
        "Its full instruction history, in order:"
    ]

    for index, entry in enumerate(history):
        text = entry.get("request", "")
        if index == 0:
            lines.append(f"- The user first asked: {text}")
        else:
            lines.append(f"- Then they asked: {text}")

    lines.append(f"- Now they ask: {current_request}")

    tokens = []

    if design.get("background"):
        tokens.append(f"background {design['background']}")
    if design.get("colors"):
        tokens.append("colours " + ", ".join(design["colors"]))
    if design.get("fonts"):
        tokens.append("fonts " + ", ".join(design["fonts"]))
    if design.get("elements"):
        tokens.append("elements present: " + ", ".join(design["elements"]))

    if tokens:
        lines.append(
            "Locked design of the current project: "
            + "; ".join(tokens)
            + ". Keep these unless the new instruction explicitly "
            "changes them."
        )

    return "\n".join(lines)
