from graph.workflow import workflow


result = workflow.invoke(
    {
        "user_request":
        "create user authantication using python fastapi backend only.",

    "next_agent": "",

    "plan": "",

    "code": "",

    "files_created": [],

    "test_result": "",

    "entrypoint": "",


    "retry_count": 0,

    "final_response": ""

    }
)


print(result)