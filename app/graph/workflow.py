from langgraph.graph import StateGraph

from app.models.state import AgentState

from app.agents.supervisor import supervisor_agent
from app.agents.planner import planner_agent
from app.agents.developer import developer_agent
from app.agents.tester import tester_agent


graph = StateGraph(AgentState)



graph.add_node(
    "supervisor",
    supervisor_agent
)


graph.add_node(
    "planner",
    planner_agent
)


graph.add_node(
    "developer",
    developer_agent
)

graph.add_node(
    "tester",
    tester_agent
)


graph.set_entry_point(
    "supervisor"
)



def router(state):

    return state["next_agent"]



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

graph.set_finish_point(
    "tester"
)



workflow = graph.compile()