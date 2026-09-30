from typing import TypedDict


class AgentState(TypedDict):

    user_request: str

    plan: str

    code: str

    test_result: str

    final_response: str