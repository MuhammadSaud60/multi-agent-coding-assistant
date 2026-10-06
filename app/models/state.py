from typing import TypedDict


class AgentState(TypedDict):

    user_request: str

    plan: str

    files_created: list[str]

    entrypoint: str

    test_command: str

    working_directory: str

    language: str

    framework: str

    test_result: str

    retry_count: int

    next_agent: str

    final_response: str