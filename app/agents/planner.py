from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama  # local
from models.state import AgentState
from dotenv import load_dotenv
import os

load_dotenv()

model = os.getenv('model')



# llm = ChatGoogleGenerativeAI(
#     model=model,
   
#     temperature=0
# )

llm = ChatOllama(
    model='llama3.2:3b',
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