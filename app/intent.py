"""Request-intent helpers shared by the supervisor and the entry points.

The supervisor routes build requests to the planner/developer, but a
request whose ONLY intent is to run, test or check the project that
already sits in the workspace ("run the code", "test the project")
must go straight to the tester. Regenerating code for such a request
destroys the user's existing project, which is exactly the bug this
detector exists to prevent.

Detection is deliberately rule-based rather than model-based: the
entry points (CLI and GUI) must know the answer BEFORE the graph
starts, because they decide whether the workspace may be cleared,
and a small local model cannot be trusted with that decision.
"""

import re

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


def is_test_only_request(request) -> bool:
    """True when the request only asks to run/test the existing project.

    Conservative on purpose: a misfire here would skip a build the
    user actually asked for, so a run verb AND a reference to existing
    work (or a bare run command like "run it") are both required, and
    any build verb disqualifies the request.
    """

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
