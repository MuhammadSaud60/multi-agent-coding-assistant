"""Deterministic preservation guard for edit runs.

Round 9 made follow-ups edit the existing project, but the model
still rewrote whole files with a fresh design: told to "add some
shapes", it returned a new page WITHOUT the heading the user never
asked to remove. Prompts (app/memory.py context plus the developer's
DO NOT list) make that less likely; this guard makes it caught.

After the developer (and after any debugger pass that writes files)
the guard compares the project as it stood when the edit run started
(state's existing_files snapshot) with the files now on disk:

1. Visible-text preservation: for every pre-existing HTML file, the
   visible text chunks (tags/scripts/styles stripped, whitespace
   normalised, chunks of 3+ characters) are extracted before and
   after. If the request itself carries remove/delete/replace
   wording the check is skipped — removal was asked for. Otherwise,
   losing more than 40% of a file's chunks is a preservation failure
   that names the missing chunks.
2. Stylesheet preservation: if the old entrypoint linked a local
   stylesheet that existed, the new entrypoint must still link a
   local stylesheet that exists.

The guard runs in EDIT mode only — never on fresh builds, test-only,
delete or question runs. On failure the workflow routes ONE
corrective pass to the debugger with a precise complaint (see
preservation_router); the state flag preservation_retried caps it at
one pass per run, and after that pass the run proceeds to the tester
whatever the guard then says. No loops.
"""

from __future__ import annotations

import posixpath
import re
from html import unescape
from typing import NamedTuple

from app.models.state import AgentState
from app.tools.workspace_tools import load_existing_files

# A chunk is a run of visible text between tags; shorter scraps
# (a stray "OK", a lone separator) are too small to protect.
_MIN_CHUNK_LENGTH = 3

# Losing up to this fraction of a file's text chunks is tolerated
# (an edit may legitimately reword a line or two); losing more
# means existing content was removed wholesale.
_MAX_MISSING_FRACTION = 0.40

# Request wording that asks for content to go away: the text check
# is skipped entirely, because the removal was the instruction.
_REMOVAL_WORDING_RE = re.compile(r"\b(remove|delete|replace)\b")

_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL
)
_STYLE_RE = re.compile(
    r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_SPLIT_RE = re.compile(r"<[^>]+>")
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_HREF_RE = re.compile(
    r"""href\s*=\s*"([^"]*)"|href\s*=\s*'([^']*)'""", re.IGNORECASE
)
_REL_RE = re.compile(
    r"""rel\s*=\s*"([^"]*)"|rel\s*=\s*'([^']*)'""", re.IGNORECASE
)

_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:")


class PreservationResult(NamedTuple):
    """Outcome of one guard check.

    ok:        True when nothing protected was lost.
    detail:    Short human-readable summary of the failure(s).
    missing:   The visible-text chunks that were lost (text check).
    complaint: The precise instruction for the corrective pass.
    """

    ok: bool
    detail: str
    missing: list
    complaint: str


def visible_text_chunks(html_text: str) -> list[str]:
    """The visible text of an HTML document, as normalised chunks.

    Scripts, styles and comments are removed first (their text is
    not visible page content), then the text between tags is split
    into chunks with whitespace collapsed. Chunks shorter than 3
    characters are dropped.
    """

    text = _SCRIPT_RE.sub(" ", html_text)
    text = _STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)

    chunks = []

    for segment in _TAG_SPLIT_RE.split(text):
        chunk = " ".join(unescape(segment).split())
        if len(chunk) >= _MIN_CHUNK_LENGTH:
            chunks.append(chunk)

    return chunks


def _stylesheet_hrefs(html_text: str) -> list[str]:
    """Local stylesheet hrefs linked by an HTML document."""

    hrefs = []

    for tag in _LINK_TAG_RE.findall(html_text):
        rel_match = _REL_RE.search(tag)
        if not rel_match:
            continue
        rel = (rel_match.group(1) or rel_match.group(2) or "").lower()
        if "stylesheet" not in rel:
            continue
        href_match = _HREF_RE.search(tag)
        if not href_match:
            continue
        href = (href_match.group(1) or href_match.group(2) or "").strip()
        if href and not href.startswith(_EXTERNAL_PREFIXES):
            hrefs.append(href)

    return hrefs


def _resolve_href(entrypoint: str, href: str) -> str:
    """Resolve a link href against the entrypoint's folder."""

    clean = href.split("?", 1)[0].split("#", 1)[0].lstrip("/")

    return posixpath.normpath(
        posixpath.join(posixpath.dirname(entrypoint), clean)
    )


def check_preservation(
    existing_files: list,
    current_files: list,
    entrypoint: str,
    request: str,
) -> PreservationResult:
    """Compare the pre-run snapshot with the files now on disk."""

    old = {
        item.get("path", ""): item.get("content", "")
        for item in existing_files
    }
    new = {
        item.get("path", ""): item.get("content", "")
        for item in current_files
    }

    failures = []
    missing: list[str] = []

    # 1. Visible-text preservation for pre-existing HTML files.
    if not _REMOVAL_WORDING_RE.search(request.lower()):
        for path, old_content in old.items():
            if not path.lower().endswith((".html", ".htm")):
                continue

            old_chunks = visible_text_chunks(old_content)
            if not old_chunks:
                continue

            new_chunks = {
                chunk.lower()
                for chunk in visible_text_chunks(new.get(path, ""))
            }
            lost = [
                chunk
                for chunk in old_chunks
                if chunk.lower() not in new_chunks
            ]

            if len(lost) / len(old_chunks) > _MAX_MISSING_FRACTION:
                failures.append(
                    f"{path}: {len(lost)} of {len(old_chunks)} "
                    "visible text chunks were removed"
                )
                missing.extend(lost)

    # 2. Stylesheet preservation for the entrypoint.
    if entrypoint and entrypoint in old:
        linked_before = [
            href
            for href in _stylesheet_hrefs(old[entrypoint])
            if _resolve_href(entrypoint, href) in old
        ]
        if linked_before:
            linked_after = [
                href
                for href in _stylesheet_hrefs(new.get(entrypoint, ""))
                if _resolve_href(entrypoint, href) in new
            ]
            if not linked_after:
                failures.append(
                    f"{entrypoint}: the stylesheet it linked "
                    f"({', '.join(linked_before)}) is no longer "
                    "linked, or the stylesheet file is gone"
                )

    if not failures:
        return PreservationResult(
            ok=True, detail="", missing=[], complaint=""
        )

    detail = "; ".join(failures)

    if missing:
        shown = "; ".join(
            chunk[:80] for chunk in missing[:10]
        )
        complaint = (
            "Your edit removed existing content the user did not "
            f"ask to remove: {shown}. Restore those elements "
            "exactly as they were, keep the change the user asked "
            f"for ({request}), and keep the current design "
            "(colours, fonts, layout)."
        )
    else:
        complaint = (
            "Your edit broke page structure the user did not ask "
            f"to change: {detail}. Restore it exactly as it was, "
            "keep the change the user asked for "
            f"({request}), and keep the current design "
            "(colours, fonts, layout)."
        )

    return PreservationResult(
        ok=False, detail=detail, missing=missing, complaint=complaint
    )


def preservation_node(state: AgentState) -> dict:
    """Graph node: run the guard (edit runs only) after file writes.

    On a first failure it arms the single corrective pass by storing
    the complaint for the debugger and setting preservation_retried;
    the router then sends the run to the debugger instead of the
    tester. Every other outcome (pass, non-edit run, corrective pass
    already used) just records the note and proceeds to the tester.
    """

    if not state.get("edit_mode") or state.get("test_only"):
        return {}

    result = check_preservation(
        state.get("existing_files", []),
        load_existing_files(),
        state.get("entrypoint", ""),
        state.get("user_request", ""),
    )

    if result.ok:
        note = (
            "PASSED after corrective pass"
            if state.get("preservation_retried")
            else "PASSED"
        )
        print(f"[PRESERVATION] {note}")
        return {
            "preservation_note": note,
            "preservation_complaint": "",
        }

    if state.get("preservation_retried"):
        # The one corrective pass already ran: report honestly and
        # let the tester judge what is actually on disk. No loops.
        # The complaint is force-cleared here too, so a stale one
        # can never route the run back to the debugger.
        note = f"FAILED after corrective pass: {result.detail}"
        print(f"[PRESERVATION] {note}")
        return {
            "preservation_note": note,
            "preservation_complaint": "",
        }

    note = f"FAILED: {result.detail}"
    print(f"[PRESERVATION] {note}")
    print("[PRESERVATION] Routing one corrective pass to the debugger.")

    return {
        "preservation_note": note,
        "preservation_retried": True,
        "preservation_complaint": result.complaint,
    }


def preservation_router(state: AgentState) -> str:
    """Route to the debugger when a corrective pass is armed."""

    if state.get("preservation_complaint"):
        return "debugger"

    return "tester"
