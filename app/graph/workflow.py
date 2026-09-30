from langgraph.graph import StateGraph

from app.models.state import AgentState
from app.agents.planner import planner_agent



graph = StateGraph(AgentState)


graph.add_node(
    "planner",
    planner_agent
)


graph.set_entry_point(
    "planner"
)


graph.set_finish_point(
    "planner"
)


workflow = graph.compile()