from app.config import get_llm
from app.memory import format_memory_context
from app.models.state import AgentState
from app.text_utils import to_text

llm = get_llm("planner")


def planner_agent(state: AgentState):

    request = state["user_request"]

    edit_context = ""

    if state.get("edit_mode") and state.get("existing_files"):
        # A follow-up instruction: plan a change to the project that
        # already exists, not a new build. The plan must start from
        # what does NOT change: an edit that silently drops existing
        # content or restyles the page is the failure this structure
        # exists to prevent.
        names = ", ".join(
            item.get("path", "") for item in state["existing_files"]
        )

        memory_block = format_memory_context(
            state.get("project_memory"), request
        )

        edit_context = f"""
        This is a change to an EXISTING project. Its current files
        are: {names}.

        {memory_block}

        Structure your plan in exactly two parts, in this order:
        1. "Unchanged:" — list everything that stays exactly as it
           is: the existing text, headings and elements, the layout,
           the colour palette, the fonts and the background.
        2. "Change:" — the single requested change, and nothing
           else. Plan the smallest set of edits that applies it and
           leaves everything in part 1 untouched.
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
