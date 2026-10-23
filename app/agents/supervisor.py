from app.config import get_llm
from app.models.state import AgentState
from app.text_utils import to_text

llm = get_llm("supervisor")


def supervisor_agent(state: AgentState):

    request = state["user_request"]

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
        "next_agent": decision
    }
