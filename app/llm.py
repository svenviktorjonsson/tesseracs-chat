import sys
from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain.memory import ConversationBufferMemory
from langchain_core.runnables import RunnablePassthrough, Runnable, RunnableLambda
from langchain_core.messages import BaseMessage
from typing import List, Callable

from . import config # Use relative import

# --- LangChain Setup ---
try:
    model = OllamaLLM(model=config.MODEL_ID, base_url=config.OLLAMA_BASE_URL)
    print(f"Successfully initialized OllamaLLM: {config.MODEL_ID} at {config.OLLAMA_BASE_URL}")
except Exception as e:
    print(f"CRITICAL ERROR: OllamaLLM init failed: {e}")
    sys.exit(1)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful AI assistant chatting in a web interface. Answer the user's questions concisely. Always use katex for math ($...$ or $$...$$). For a literal dollar sign use \\$. When providing code, use standard markdown code blocks (e.g., ```python ... ```)."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])
output_parser = StrOutputParser()

def create_chain(memory_loader_func: Callable[[dict], List[BaseMessage]]) -> Runnable:
    """
    Creates the LangChain processing chain using a provided memory loading function.
    """
    chain = (
        RunnablePassthrough.assign(history=RunnableLambda(memory_loader_func))
        | prompt
        | model
        | output_parser
    )
    return chain

def get_model() -> OllamaLLM:
    """Returns the initialized Ollama LLM model."""
    return model