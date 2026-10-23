from app.config import get_llm
from app.models.state import AgentState

llm = get_llm("planner")


def planner_agent(state: AgentState):

    request = state["user_request"]

    prompt = f"""
        You are a software architect.
        Analyze this request:

        {request}

        Create a development plan.
        """

    response = llm.invoke(prompt)

    return {
        "plan": response.content
    }
