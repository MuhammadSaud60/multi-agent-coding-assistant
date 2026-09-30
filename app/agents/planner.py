from langchain_google_genai import ChatGoogleGenerativeAI
from app.models.state import AgentState
from dotenv import load_dotenv

load_dotenv()


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)


def planner_agent(state: AgentState):

    request = state["user_request"]

    prompt = f"""
        You are a software architect.
        Analyze this request:

        {request}

        Create a development plan.
        """

    response = llm.invoke(prompt)


    return {
        "plan": response.content
    }