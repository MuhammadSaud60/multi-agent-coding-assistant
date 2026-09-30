from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from app.tools.code_tools import run_python_file

from app.tools.file_tools import (
    create_file,
    read_file
)


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)



tools = [
    create_file,
    read_file,
    run_python_file

]



agent = create_agent(
    model=llm,
    tools=tools
)



def developer_agent(state):

    plan = state.get("plan")

    if not plan:
        plan = state["user_request"]


    response = agent.invoke(
        {
            "messages": [
                {
                    "role":"user",
                    "content":
                    f"""
                        You are a senior software developer.

                        Your task:
                        {plan}

                        You must use tools.

                        If a file needs to be created:
                        1. Decide filename
                        2. Write complete content
                        3. Call create_file tool

                        Do not only explain code.
                        Actually create the file.
                        """
                }
            ]
        }
    )


    return {
        "code":
        response["messages"][-1].content
    }