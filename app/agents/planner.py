from app.config import get_llm
from app.models.state import AgentState
from app.text_utils import to_text

llm = get_llm("planner")


def planner_agent(state: AgentState):

    request = state["user_request"]

    edit_context = ""

    if state.get("edit_mode") and state.get("existing_files"):
        # A follow-up instruction: plan a change to the project that
        # already exists, not a new build.
        names = ", ".join(
            item.get("path", "") for item in state["existing_files"]
        )
        edit_context = f"""
        This is a change to an EXISTING project. Its current files
        are: {names}. Plan the smallest set of edits that applies
        the requested change and leaves everything else untouched.
        """

    prompt = f"""
        You are a software architect.
        Analyze this request:

        {request}
        {edit_context}
        Create a development plan.
        """

    response = llm.invoke(prompt)

    return {
        # Coerce: a list-of-blocks content stored raw in the state
        # would break every string consumer downstream (the developer
        # prompt, the CLI's plan printout, the GUI's event payload).
        "plan": to_text(response.content)
    }
