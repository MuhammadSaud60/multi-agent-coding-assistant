from langgraph.graph import StateGraph, END

from app.models.state import AgentState

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


def test_router(state):

    result = state.get("test_result", "")
    retry_count = state.get("retry_count", 0)

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
        "developer": "developer"
    }
)


graph.add_edge(
    "planner",
    "developer"
)


graph.add_edge(
    "developer",
    "tester"
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