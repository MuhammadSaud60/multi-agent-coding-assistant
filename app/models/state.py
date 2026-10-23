from typing import TypedDict


class AgentState(TypedDict):

    user_request: str

    plan: str

    files_created: list[str]

    entrypoint: str

    entrypoint_note: str

    test_command: str

    working_directory: str

    language: str

    framework: str

    test_result: str

    retry_count: int

    next_agent: str

    test_only: bool

    # True when this run edits the project already in the workspace
    # (a follow-up instruction) instead of building from scratch.
    edit_mode: bool

    # The workspace files as they stood when an edit run started:
    # [{"path": ..., "content": ...}, ...]. Empty for fresh builds.
    existing_files: list

    final_response: str