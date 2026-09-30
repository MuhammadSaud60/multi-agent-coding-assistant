from app.graph.workflow import workflow


result = workflow.invoke(
    {
        "user_request": 
        "Create a FastAPI authentication system",

        "plan": "",
        "code": "",
        "test_result": "",
        "final_response": ""
    }
)


print(result)