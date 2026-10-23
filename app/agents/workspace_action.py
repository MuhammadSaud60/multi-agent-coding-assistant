"""Graph node for direct workspace actions.

The entry points (CLI and GUI) intercept pure delete requests
before the graph starts, so this node is the safety net for a
request that reaches the graph anyway (a direct workflow call, a
future entry point): the supervisor routes it here and the deletion
is performed as an operation, with the outcome as the run's result,
instead of the developer generating a deletion script.
"""

from app.direct_actions import handle_delete_request
from app.models.state import AgentState


def workspace_action_agent(state: AgentState):

    outcome = handle_delete_request(state["user_request"])

    if outcome is None:
        # The supervisor only routes parsed delete requests here, so
        # this is unreachable in practice; never invent an outcome.
        outcome = "Nothing was deleted."

    print(f"\n[WORKSPACE ACTION] {outcome}")

    return {
        "test_result": outcome,
        "final_response": outcome,
    }
