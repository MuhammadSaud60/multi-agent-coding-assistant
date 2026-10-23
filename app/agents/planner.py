from app.config import get_llm
from app.models.state import AgentState
from app.text_utils import to_text

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
        # Coerce: a list-of-blocks content stored raw in the state
        # would break every string consumer downstream (the developer
        # prompt, the CLI's plan printout, the GUI's event payload).
        "plan": to_text(response.content)
    }
