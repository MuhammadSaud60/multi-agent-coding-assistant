from app.config import get_llm
from app.intent import is_delete_request, is_question, is_test_only_request
from app.models.state import AgentState
from app.text_utils import to_text

llm = get_llm("supervisor")


def supervisor_agent(state: AgentState):

    request = state["user_request"]

    # A question ("how to run it?", "how do I create a login
    # page?") is not a command at all. It is classified FIRST, so
    # the verbs inside it ("run", "create") can never route it to
    # the tester as a run order or to the developer as a build
    # order: it goes to the answerer and gets an answer.
    if is_question(request):
        print("[SUPERVISOR] Question -> answerer")
        return {
            "next_agent": "answerer",
            "test_only": False,
        }

    # A pure delete request ("delete the existing file") is an
    # operation on the workspace, not a build. The entry points
    # normally perform it before the graph starts; if one reaches
    # the graph anyway, route it to the direct action so the
    # developer never generates a deletion script for it.
    if is_delete_request(request):
        print("[SUPERVISOR] Delete request -> direct workspace action")
        return {
            "next_agent": "workspace_action",
            "test_only": False,
        }

    # A run/test-only request ("run the code") targets the project
    # that is already in the workspace. Sending it to the planner or
    # developer would regenerate code and destroy that project, so it
    # is routed straight to the tester without asking the model.
    if is_test_only_request(request):
        print("[SUPERVISOR] Run/test-only request -> tester (existing workspace)")
        return {
            "next_agent": "tester",
            "test_only": True,
        }

    prompt = f"""

        You are a project manager AI.

        Choose the next agent.

        Available agents:

        planner:
        For creating architecture and requirements.

        developer:
        For writing or modifying code.


        User request:

        {request}


        Return only one word:

        planner
        or
        developer

        """

    response = llm.invoke(prompt)

    # response.content is not always a string: LangChain 1.x models can
    # return a list of content blocks, and .strip() on a list crashed
    # the run with AttributeError. Coerce to plain text first.
    decision = to_text(response.content).strip().lower()

    # The graph router only accepts "planner" or "developer"; anything
    # else the model returns is normalised here instead of crashing it.
    if "developer" in decision:
        decision = "developer"
    else:
        decision = "planner"

    return {
        "next_agent": decision,
        "test_only": False,
    }
