"""Coerce model output to plain text.

Chat models do not always hand back a plain string. With LangChain 1.x
a message's ``content`` can be a LIST of content blocks, e.g.
``[{"type": "text", "text": "planner"}]``, and small local models
answering a structured-output schema sometimes return a field as a
list of lines instead of one string. Code that assumes a string and
calls ``.strip()`` / ``.startswith()`` / ``+`` on such a value dies with
``AttributeError: 'list' object has no attribute 'strip'``.

Every agent that consumes model-derived text goes through ``to_text``
first, so the list shape can never crash a run again:

- ``str`` stays as-is.
- ``None`` becomes ``""``.
- A list/tuple is flattened part by part and joined with ``joiner``:
  dict blocks contribute their ``"text"`` value, plain strings
  contribute themselves, anything else goes through ``str()``.
- A single dict block contributes its ``"text"`` value (a block with
  no text, such as an image block, contributes ``""``).
- Anything else is converted with ``str()``.
"""

from __future__ import annotations

from typing import Any


def to_text(value: Any, *, joiner: str = "") -> str:
    """Return `value` as plain text, whatever shape the model used."""

    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (list, tuple)):
        return joiner.join(
            to_text(part, joiner=joiner) for part in value
        )

    if isinstance(value, dict):
        # One content block: {"type": "text", "text": "..."}.
        text = value.get("text")
        if text is None:
            return ""
        return to_text(text, joiner=joiner)

    return str(value)
