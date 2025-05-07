# app/llm.py
import sys
import os # Import os for environment variable check
from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
# from langchain.memory import ConversationBufferMemory # Not used directly here
from langchain_core.runnables import RunnablePassthrough, Runnable, RunnableLambda
from langchain_core.messages import BaseMessage
from typing import List, Callable, Optional

# Use absolute import based on project structure if needed, or relative
# Assuming config is in the same directory or Python path is set correctly
from . import config

# --- LangChain Setup ---

# Global variable to hold the model instance
_model_instance: Optional[OllamaLLM] = None
_last_model_id_used: Optional[str] = None
_last_base_url_used: Optional[str] = None

def get_model() -> OllamaLLM:
    """
    Initializes and returns the Ollama LLM model instance.
    Re-initializes if config changes (relevant with Uvicorn reload).
    """
    global _model_instance, _last_model_id_used, _last_base_url_used

    # Check if config has changed since last initialization
    # This is important because Uvicorn reload might not fully re-import everything
    config_changed = (
        _model_instance is None or
        config.MODEL_ID != _last_model_id_used or
        config.OLLAMA_BASE_URL != _last_base_url_used
    )

    if config_changed:
        print(f"DEBUG get_model: Configuration changed or first init. Initializing OllamaLLM with model='{config.MODEL_ID}' at base_url='{config.OLLAMA_BASE_URL}'")
        try:
            _model_instance = OllamaLLM(model=config.MODEL_ID, base_url=config.OLLAMA_BASE_URL)
            _last_model_id_used = config.MODEL_ID
            _last_base_url_used = config.OLLAMA_BASE_URL
            print(f"Successfully initialized/updated OllamaLLM: {_last_model_id_used} at {_last_base_url_used}")
        except Exception as e:
            print(f"CRITICAL ERROR: OllamaLLM init failed in get_model: {e}")
            # Re-raise the exception so the calling function knows initialization failed
            raise e
    # else:
    #     print(f"DEBUG get_model: Using existing OllamaLLM instance for model='{_last_model_id_used}'")


    if _model_instance is None:
         # This should ideally not happen if the try/except above works, but as a safeguard:
         print("CRITICAL ERROR: _model_instance is None after attempting initialization in get_model.")
         sys.exit(1) # Or raise a more specific error

    return _model_instance

# Define prompt structure (can stay global)
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful AI assistant chatting in a web interface. Answer the user's questions concisely. Always use katex for math ($...$ or $$...$$). For a literal dollar sign use \\$. When providing code, use standard markdown code blocks (e.g., ```python ... ```)."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])

# Define output parser (can stay global)
output_parser = StrOutputParser()

def create_chain(memory_loader_func: Callable[[dict], List[BaseMessage]]) -> Runnable:
    """
    Creates the LangChain processing chain using a provided memory loading function.
    Ensures the model is initialized before creating the chain.
    """
    try:
        # Get the (potentially re-initialized) model instance
        current_model = get_model()
    except Exception as model_init_error:
        # Propagate the error if model initialization failed
        print(f"ERROR in create_chain: Failed to get model instance: {model_init_error}")
        raise model_init_error # Re-raise to prevent chain creation with bad model

    chain = (
        RunnablePassthrough.assign(history=RunnableLambda(memory_loader_func))
        | prompt
        | current_model # Use the obtained model instance
        | output_parser
    )
    return chain

# Remove the old global initialization attempt
# try:
#     model = OllamaLLM(model=config.MODEL_ID, base_url=config.OLLAMA_BASE_URL)
#     print(f"Successfully initialized OllamaLLM: {config.MODEL_ID} at {config.OLLAMA_BASE_URL}")
# except Exception as e:
#     print(f"CRITICAL ERROR: OllamaLLM init failed: {e}")
#     sys.exit(1)
