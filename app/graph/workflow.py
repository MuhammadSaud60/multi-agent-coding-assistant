from langgraph.graph import StateGraph, END

from app.models.state import AgentState
from app.text_utils import to_text

from app.agents.supervisor import supervisor_agent
from app.agents.planner import planner_agent
from app.agents.developer import developer_agent
from app.agents.tester import tester_agent
from app.agents.debugger import debugger_agent


graph = StateGraph(AgentState)


graph.add_node("supervisor", supervisor_agent)
graph.add_node("planner", planner_agent)
graph.add_node("developer", developer_agent)
graph.add_node("tester", tester_agent)
graph.add_node("debugger", debugger_agent)


graph.set_entry_point("supervisor")


def router(state):

    return state["next_agent"]


def developer_router(state):

    # The developer never raises on a bad model payload: when the
    # payload is unusable it returns a terminal FAILED test_result
    # itself. There is then nothing for the tester (or the debugger)
    # to do, so the run ends with that honest message.
    if state.get("test_result"):
        print("\n[GRAPH] DEVELOPER PAYLOAD UNUSABLE → END")
        return "finish"

    return "tester"


def test_router(state):

    # Coerced defensively: this router calls .startswith() on the
    # value, so it must be text even if a state producer ever stores
    # a list-shaped model response here.
    result = to_text(state.get("test_result", ""))
    retry_count = state.get("retry_count", 0)

    if result.startswith("BLOCKED"):
        # The tester refused to run destructive generated code. Retrying
        # can never fix that, so the run ends here with the honest
        # BLOCKED result as its final response.
        print("\n[GRAPH] TEST BLOCKED → END (never retried)")
        return "finish"

    if state.get("test_only"):
        # A run/test-only request reports the existing project's result
        # as-is. The debugger exists to fix code the developer just
        # wrote; it must never rewrite the user's existing project
        # unasked, so the run ends here whatever the outcome was.
        print("\n[GRAPH] TEST-ONLY RUN → END")
        return "finish"

    if result.startswith("STATUS: PASSED"):
        print("\n[GRAPH] TEST PASSED → END")
        return "finish"

    if retry_count >= 3:
        print("\n[GRAPH] MAX RETRIES → END")
        return "finish"

    print(
        f"\n[GRAPH] TEST FAILED → DEBUGGER "
        f"(retry {retry_count + 1}/3)"
    )

    return "debugger"



graph.add_conditional_edges(
    "supervisor",
    router,
    {
        "planner": "planner",
        "developer": "developer",
        # Run/test-only requests skip generation entirely and exercise
        # the project already sitting in the workspace.
        "tester": "tester"
    }
)


graph.add_edge(
    "planner",
    "developer"
)


graph.add_conditional_edges(
    "developer",
    developer_router,
    {
        "tester": "tester",
        "finish": END
    }
)


graph.add_conditional_edges(
    "tester",
    test_router,
    {
        "debugger": "debugger",
        "finish": END
    }
)


graph.add_edge(
    "debugger",
    "tester"
)


workflow = graph.compile()