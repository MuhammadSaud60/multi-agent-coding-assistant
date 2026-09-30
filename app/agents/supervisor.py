from langchain_google_genai import ChatGoogleGenerativeAI
from app.models.state import AgentState
from dotenv import load_dotenv
import os

load_dotenv()

model = os.getenv('model')

llm = ChatGoogleGenerativeAI(
    model=model,
    temperature=0
)


def supervisor_agent(state: AgentState):

    request = state["user_request"]


    prompt = f"""

        You are a project manager AI.

        Choose the next agent.

        Available agents:

        planner:
        For creating architecture and requirements.

        developer:
        For writing or modifying code.


        User request:

        {request}


        Return only one word:

        planner
        or
        developer

        """


    response = llm.invoke(prompt)


    decision = response.content.strip().lower()


    return {
        "next_agent": decision
    }