"""Request-intent helpers shared by the supervisor and the entry points.

The supervisor routes build requests to the planner/developer, but a
request whose ONLY intent is to run, test or check the project that
already sits in the workspace ("run the code", "test the project")
must go straight to the tester. Regenerating code for such a request
destroys the user's existing project, which is exactly the bug this
detector exists to prevent.

The same applies to direct file-management requests ("delete the
existing file", "delete notes.txt", "delete all files"): the user is
asking for an OPERATION on the workspace, not for a project to be
built. Routing those to the developer made it generate a deletion
script (which the tester then, correctly, blocked as destructive)
while the file the user meant stayed exactly where it was.
parse_delete_request() recognises them so the entry points (and the
supervisor, as a safety net) can perform the deletion directly.

And some requests are neither builds nor operations: they are
QUESTIONS ("how to run it?", "how do I create a login page?"). A
question must be answered, never acted on: it must not trigger the
test-only path (the original "how to run it?" bug: classified as a
run command, it pointlessly re-validated the project), the delete
path, or a build. is_question() therefore runs FIRST in every
detector below: a question is classified before any command verb
inside it gets a chance to match.

Finally, build requests split in two. A FOLLOW-UP that modifies the
project already in the workspace ("add some shapes", "make the
circle bigger") is an EDIT: the workspace is kept and the developer
works from the existing files. A request that explicitly starts
over ("start over", "from scratch", "naya project") is a FRESH
START and keeps the old behaviour. is_fresh_start_request() and
is_edit_request() below draw that line for the entry points, which
must decide before the graph runs whether the workspace may be
cleared.

Detection is deliberately rule-based rather than model-based: the
entry points (CLI and GUI) must know the answer BEFORE the graph
starts, because they decide whether the workspace may be cleared,
and a small local model cannot be trusted with that decision.
"""

import re
from typing import NamedTuple

from app.text_utils import to_text

# Verbs that ask for existing code to be exercised, not written.
_RUN_VERBS = {
    "run",
    "test",
    "execute",
    "check",
    "launch",
    "rerun",
    "restart",
}

# Verbs that ask for code to be created or changed. Any of these makes
# the request a build request, even if it also mentions running
# ("create X and run it" builds first; the normal flow tests after).
_BUILD_VERBS = {
    "create",
    "build",
    "make",
    "write",
    "add",
    "generate",
    "implement",
    "develop",
    "fix",
    "change",
    "modify",
    "update",
    "design",
    "delete",
    "remove",
}

# Words that refer to the thing that already exists in the workspace.
_EXISTING_REFERENCES = {
    "code",
    "it",
    "this",
    "that",
    "project",
    "app",
    "script",
    "program",
    "website",
    "page",
    "file",
    "files",
    "again",
    "existing",
    "current",
}


# ----------------------------------------------------------------------
# Questions
# ----------------------------------------------------------------------

# First words that make a request a question rather than a command.
# "how do I create a login page?" asks for an explanation; treating
# it as a build order (or "how to run it?" as a run order) is the bug
# this detector exists to prevent.
_QUESTION_OPENERS = {
    "how",
    "what",
    "why",
    "when",
    "where",
    "which",
    "who",
    "can",
    "could",
    "should",
    "do",
    "does",
    "is",
    "are",
    "explain",
}

# Multi-word question openers that are not single command words.
_QUESTION_PHRASES = ("tell me", "help me understand")


def is_question(request) -> bool:
    """True when the request asks a question instead of giving an order.

    A trailing "?" always counts, as does an opener like "how",
    "what", "why" or "can you explain". Classified BEFORE every
    command detector, so the verbs inside a question ("run" in "how
    to run it?", "create" in "how do I create a login page?") never
    turn it into an action.
    """

    text = to_text(request).strip().lower()

    if not text:
        return False

    if text.endswith("?"):
        return True

    words = re.findall(r"[a-z']+", text)

    if not words:
        return False

    if words[0] in _QUESTION_OPENERS:
        return True

    return any(text.startswith(phrase) for phrase in _QUESTION_PHRASES)


def is_test_only_request(request) -> bool:
    """True when the request only asks to run/test the existing project.

    Conservative on purpose: a misfire here would skip a build the
    user actually asked for, so a run verb AND a reference to existing
    work (or a bare run command like "run it") are both required, and
    any build verb disqualifies the request. A question is never a
    run command ("how to run it?" asks HOW, it does not order a run).
    """

    if is_question(request):
        return False

    words = re.findall(r"[a-z']+", to_text(request).lower())

    if not words:
        return False

    # Roman Urdu phrasing Saud used for the same intent:
    # "chala ke dekho" / "chala kar dekho" = "run it and see".
    if "chala" in words and ("dekho" in words or "do" in words):
        return True

    if any(word in _BUILD_VERBS for word in words):
        return False

    if not any(word in _RUN_VERBS for word in words):
        return False

    return (
        any(word in _EXISTING_REFERENCES for word in words)
        or len(words) <= 3
    )


# ----------------------------------------------------------------------
# Delete / file-management requests
# ----------------------------------------------------------------------

# Verbs that ask for a file to be removed from the workspace.
_DELETE_VERBS = {
    "delete",
    "remove",
    "erase",
}

# Build verbs OTHER than the delete verbs themselves. Any of these
# makes the request a build request that merely mentions deletion
# ("delete the file and create a fresh one", "fix the bug and remove
# the workaround") and it must follow the normal planner/developer
# path, never the direct action.
_PURE_BUILD_VERBS = _BUILD_VERBS - _DELETE_VERBS

# Words that widen a delete request to the whole workspace.
_ALL_WORDS = {
    "all",
    "every",
    "everything",
    # Roman Urdu: "sab files" / "sare files" = "all the files".
    "sab",
    "sara",
    "sare",
    "saari",
}

# A filename as users actually type one: notes.txt, main.py,
# index.html, src/app.js. Matched against the raw request text.
_FILENAME_RE = re.compile(r"[\w][\w.\-/]*\.[a-z0-9]{2,10}")


class DeleteIntent(NamedTuple):
    """A pure workspace-deletion request, as parsed from the text.

    kind:
      "named" — the request names one specific file (filename set).
      "all"   — the wording covers the whole workspace ("all",
                "everything", "sab", or the plural "files" with no
                name: "delete the existing files").
      "plain" — a delete of "the file" with no name and no all-word;
                what it means depends on how many files the workspace
                holds, which only the action layer can see.
    """

    kind: str
    filename: str | None


def parse_delete_request(request) -> DeleteIntent | None:
    """Parse a pure delete request, or return None.

    Conservative in the same way as is_test_only_request: a delete
    verb is required, and any other build verb disqualifies the
    request, so a build that happens to mention deleting something
    is never hijacked into a direct file operation. A question is
    never a delete order either ("can I delete this file?" asks;
    it does not command).
    """

    if is_question(request):
        return None

    text = to_text(request).lower()
    words = re.findall(r"[a-z']+", text)

    if not words:
        return None

    if not any(word in _DELETE_VERBS for word in words):
        return None

    if any(word in _PURE_BUILD_VERBS for word in words):
        return None

    match = _FILENAME_RE.search(text)

    if match:
        return DeleteIntent(kind="named", filename=match.group(0))

    if any(word in _ALL_WORDS for word in words) or "files" in words:
        return DeleteIntent(kind="all", filename=None)

    # "delete the existing file" / "delete it": a plain delete whose
    # target the workspace contents must decide.
    if "file" in words or any(
        word in _EXISTING_REFERENCES for word in words
    ):
        return DeleteIntent(kind="plain", filename=None)

    return None


def is_delete_request(request) -> bool:
    """True when the request is a pure workspace-deletion request."""

    return parse_delete_request(request) is not None


# ----------------------------------------------------------------------
# Fresh-start and edit requests
# ----------------------------------------------------------------------

# Phrasing that asks for the existing project to be thrown away and
# a brand-new one built in its place. These keep the old behaviour
# (previous project moved to the backup folder, build from scratch)
# even when the workspace is occupied.
_FRESH_START_PHRASES = (
    "new project",
    "start over",
    "start fresh",
    "fresh start",
    "from scratch",
    # Roman Urdu: "make it again from the start".
    "naya project",
    "dobara se banao",
    "phir se banao",
)

_FRESH_START_WORDS = {
    "scrap",
    "discard",
}

# Verbs that change work that already exists rather than create new
# work. A follow-up instruction ("add some shapes", "fix the layout",
# "update the title") is an EDIT of the project in the workspace:
# clearing it and regenerating from scratch destroys the very thing
# the user is asking to change, which is the second-instruction bug
# these detectors exist to prevent.
_CHANGE_VERBS = {
    "add",
    "update",
    "change",
    "modify",
    "fix",
    "move",
    "resize",
    "rename",
    "replace",
    "adjust",
    "tweak",
    "improve",
    "extend",
    "append",
    "insert",
    "increase",
    "decrease",
    "enlarge",
    "shrink",
    # Not a pure delete by the time this detector runs (the delete
    # flow classifies first and disqualifies itself above), so a
    # "remove" here is part of a build: "remove the footer and add
    # a header" edits the existing page.
    "remove",
}

# Comparative adjectives: "make the circle bigger" names no change
# verb, but the comparative only makes sense against the existing
# project.
_COMPARATIVES = {
    "bigger",
    "smaller",
    "larger",
    "faster",
    "slower",
    "darker",
    "lighter",
    "brighter",
    "wider",
    "narrower",
    "taller",
    "shorter",
    "longer",
    "thicker",
    "thinner",
}

# (Requests whose only signal is a creation verb — "build a small
# website", "create a second script" — fall through to False below:
# a fresh build, not an edit, even when the workspace happens to be
# occupied. The entry points keep the established behaviour for
# them: back the old project up, build anew.)


def is_fresh_start_request(request) -> bool:
    """True when the request explicitly asks to start over.

    "start over and make a new project", "build it from scratch",
    "scrap this" — the previous project is backed up and the build
    starts from an empty workspace, exactly as builds always did.
    A question is never a fresh-start order.
    """

    if is_question(request):
        return False

    text = to_text(request).lower()

    if any(phrase in text for phrase in _FRESH_START_PHRASES):
        return True

    words = re.findall(r"[a-z']+", text)

    return any(word in _FRESH_START_WORDS for word in words)


def is_edit_request(request) -> bool:
    """True when a build request modifies the existing project.

    The signal is modification language: a change verb ("add",
    "update", "fix", "rename"), a comparative ("make the circle
    bigger"), or "make" aimed at an existing thing ("make it
    faster", "make the header blue"). Creation-only requests
    ("build a small website") and explicit fresh starts are NOT
    edits, and neither are questions, run/test-only requests or
    pure deletes — those flows are classified before this detector
    is ever consulted, and each of them disqualifies itself here
    too so the helper is safe to call standalone.
    """

    if is_question(request):
        return False

    if is_fresh_start_request(request):
        return False

    if is_test_only_request(request):
        return False

    if is_delete_request(request):
        return False

    words = re.findall(r"[a-z']+", to_text(request).lower())

    if not words:
        return False

    if any(
        word in _CHANGE_VERBS or word in _COMPARATIVES for word in words
    ):
        return True

    # "make the circle bigger" / "make it faster": "make" pointed at
    # a definite existing thing is a change, not a creation.
    if "make" in words:
        index = words.index("make")
        if index + 1 < len(words) and words[index + 1] in {
            "the",
            "it",
            "this",
            "that",
        }:
            return True

    return False
