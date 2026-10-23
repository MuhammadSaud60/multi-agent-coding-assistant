"""Answering questions about the user's project.

A question ("how to run it?", "how do I create a login page?") is
not a command, and until this module existed the assistant had no
way to respond to one: "how to run it?" was classified as a
run/test order (the word "run" matched), so the tester pointlessly
re-validated the project instead of answering, and a question about
building could be mistaken for a build order.

answer_question() gathers the facts of the project actually sitting
in the workspace (its files, its entrypoint, its kind, the last
test result when there is one), states the ground truth for running
that kind of project, and makes ONE model call with a tight prompt
so the answer addresses the user's actual question in plain
language. It never writes files, never runs anything, and never
clears the workspace. When the model returns nothing usable, the
factual ground truth is the answer, so a question always gets one.

The entry points (CLI and GUI) call answer_question() directly,
before any agent graph work; answerer_agent() is the graph node the
supervisor routes to when a question reaches the graph anyway.
"""

from app.agents.tester import pick_workspace_entrypoint, scan_workspace_files
from app.config import get_llm, get_workspace_root
from app.models.state import AgentState
from app.text_utils import to_text

llm = get_llm("answerer")


def _project_kind(entrypoint: str | None) -> str:
    if not entrypoint:
        return "none"

    lowered = entrypoint.lower()

    if lowered.endswith((".html", ".htm")):
        return "web"

    if lowered.endswith(".py"):
        return "python"

    return "other"


def _ground_truth(kind: str, entrypoint: str | None, workspace) -> str:
    """The factual core of a how-to-run answer for this project."""

    if kind == "web":
        return (
            f"To run it, open {entrypoint} in a web browser: "
            f"double-click {entrypoint} in the {workspace.name} "
            f"folder ({workspace}). No server or install is needed; "
            "the browser runs the HTML, CSS and JavaScript directly."
        )

    if kind == "python":
        return (
            f"To run it, open a terminal in the {workspace.name} "
            f"folder ({workspace}) and run: python {entrypoint}. "
            "The program's output appears in that terminal."
        )

    if kind == "none":
        return (
            "There is no project in the workspace yet, so there is "
            "nothing to run. Build something first (for example a "
            "small website or a Python script), then it can be run."
        )

    return (
        f"The project's starting file is {entrypoint}, in the "
        f"{workspace.name} folder ({workspace})."
    )


def answer_question(request, last_test_result: str = "") -> str:
    """Answer a question about the current workspace project.

    One model call, phrased around facts gathered from the
    workspace; falls back to the factual ground truth when the
    model gives nothing usable.
    """

    workspace = get_workspace_root()
    files = scan_workspace_files()
    entrypoint = pick_workspace_entrypoint(files) if files else None
    kind = _project_kind(entrypoint)
    truth = _ground_truth(kind, entrypoint, workspace)

    files_line = ", ".join(files) if files else "(workspace is empty)"

    result_line = ""

    if last_test_result:
        lines = to_text(last_test_result).strip().splitlines()
        if lines:
            result_line = f"\n        - Last test result: {lines[0]}"

    prompt = f"""
        You answer questions about the user's own project in a
        coding assistant. Answer the question below in 2 to 4 short
        sentences, in plain language. Do not generate code, do not
        describe files being created or changed, and do not claim
        anything was run or tested. When the question is about
        running the project, base the answer on the ground truth
        given here.

        Project facts:
        - Kind: {kind}
        - Entrypoint: {entrypoint or "(none)"}
        - Files: {files_line}
        - Workspace folder: {workspace}{result_line}
        - Ground truth for running this project: {truth}

        Question: {request}

        Answer:
        """

    answer = ""

    try:
        response = llm.invoke(prompt)
        answer = to_text(response.content).strip()
    except Exception:
        # A failed model call must not swallow the question: the
        # facts gathered above still answer it.
        answer = ""

    if len(answer) < 10:
        answer = truth

    return answer


def answerer_agent(state: AgentState):
    """Graph node: the question's answer is the whole run's result."""

    answer = answer_question(
        state["user_request"], state.get("test_result", "")
    )

    print(f"\n[ANSWERER] {answer}")

    return {
        "test_result": answer,
        "final_response": answer,
    }
