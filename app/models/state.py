from typing import TypedDict


class AgentState(TypedDict):

    user_request: str
    next_agent: str
    plan: str
    code: str
    files_created: list[str]
    entrypoint: str
    test_result: str
    retry_count: int
    final_response: str