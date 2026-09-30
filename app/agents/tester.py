from langchain_google_genai import ChatGoogleGenerativeAI

from app.models.state import AgentState
from app.tools.code_tools import run_python_file
from dotenv import load_dotenv
import os

load_dotenv()

model = os.getenv('model')

llm = ChatGoogleGenerativeAI(
    model=model,
    temperature=0
)


def tester_agent(state: AgentState):

    code = state["code"]


    prompt = f"""
        You are a software tester.

        Analyze this generated code:

        {code}

        Find possible bugs and testing requirements.
"""


    review = llm.invoke(prompt)


    return {
        "test_result": review.content
    }