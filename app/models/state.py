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

    # The persistent project memory (see app/memory.py) as loaded
    # when this run started: original request, full instruction
    # history and locked design tokens. Edit runs hand it to the
    # planner and developer so an edit builds on the conversation
    # instead of redesigning from scratch. Empty for fresh builds.
    project_memory: dict

    # Preservation guard (edit runs only, see app/preservation.py):
    # whether the one corrective pass has already been used, its
    # outcome note for tests/logs, and the complaint a corrective
    # debugger pass must answer ("" when there is none).
    preservation_retried: bool

    preservation_note: str

    preservation_complaint: str

    final_response: str