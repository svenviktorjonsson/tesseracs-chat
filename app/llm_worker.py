import os
import traceback
from pathlib import Path
from multiprocessing.connection import Connection
from typing import Optional, Any, List

# All LangChain and AI SDK imports are contained within the worker
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnablePassthrough, RunnableLambda
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, messages_from_dict
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

# --- Constants ---
STREAM_END_SIGNAL = "__STREAM_END__"
ERROR_PREFIX = "ERROR::"

# --- Load System Prompt from File ---
try:
    # Construct the path to the prompt file relative to this script's location
    PROMPT_FILE_PATH = Path(__file__).parent / "system_prompt.txt"
    with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    print("FATAL ERROR: system_prompt.txt not found. Please ensure it exists in the 'app' directory.")
    SYSTEM_PROMPT = "You are a helpful assistant." # Fallback to prevent crash

prompt_template = ChatPromptTemplate.from_messages([
    SystemMessage(content=SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])

output_parser = StrOutputParser()


def get_model(provider_id: str, model_id: str, api_key: Optional[str], base_url: Optional[str]) -> Optional[Any]:
    """Initializes and returns a LangChain model instance based on provider."""
    try:
        if provider_id == "openai_compatible_server":
            return ChatOpenAI(model_name=model_id, openai_api_base=base_url, openai_api_key=api_key, streaming=True)
        elif provider_id == "google_gemini":
            return None # Signal to use the native SDK wrapper
        elif provider_id == "anthropic_claude":
            return ChatAnthropic(model_name=model_id, anthropic_api_key=api_key, max_tokens=8192, streaming=True)
        else:
            return None
    except Exception as e:
        print(f"[Worker] Error initializing model: {e}")
        return None


def get_chain(model_instance: Optional[Runnable], history_messages: List[BaseMessage]) -> Runnable:
    """Constructs the LangChain runnable chain."""
    def memory_loader_func(input_dict: dict) -> List[BaseMessage]:
        return history_messages

    return (
        RunnablePassthrough.assign(history=RunnableLambda(memory_loader_func))
        | prompt_template
        | model_instance
        | output_parser
    )


def stream_with_native_google_sdk(conn: Connection, job: dict):
    """
    Handles streaming specifically for Google Gemini using its native SDK.
    """
    print("[WORKER/Google] Using native Google SDK for streaming.")
    api_key = job.get("api_key")
    if not api_key:
        print("[WORKER/Google] ERROR: API key is missing.")
        conn.send(f"{ERROR_PREFIX}Google API Key is not configured in your settings.")
        return
    
    print(f"[WORKER/Google] Configuring with API Key starting with: {api_key[:4]}...")

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(job["model_id"], system_instruction=SYSTEM_PROMPT)

        history_for_sdk = []
        for msg in job["history_messages"]:
            role = "user" if isinstance(msg, HumanMessage) else "model"
            history_for_sdk.append({'role': role, 'parts': [msg.content]})

        print("[WORKER/Google] Sending request to Google API...")
        response_stream = model.generate_content(history_for_sdk, stream=True)

        chunk_count = 0
        for chunk in response_stream:
            chunk_count += 1
            if chunk.text:
                # --- ADDED LOGGING FOR EACH CHUNK ---
                print(f"[WORKER/Google CHUNK {chunk_count}]: {repr(chunk.text)}")
                conn.send(chunk.text)
        
        print(f"[WORKER/Google] Stream finished. Received {chunk_count} chunks.")

    except Exception as e:
        print(f"[WORKER/Google] CRITICAL ERROR during API call: {e}")
        traceback.print_exc()
        
        if isinstance(e, google_exceptions.PermissionDenied):
            error_message = f"Permission Denied. This is likely due to an invalid or revoked API key. Details: {e}"
            conn.send(f"{ERROR_PREFIX}{error_message}")
        else:
            tb_str = traceback.format_exc()
            conn.send(f"{ERROR_PREFIX}Error in Google SDK stream: {e}\n{tb_str}")

def process_job(conn: Connection, job: dict):
    """Processes a single job dictionary received from the main app."""
    print(f"[Worker] Processing job for provider: {job.get('provider_id')}")
    history_messages = messages_from_dict(job["history_messages_serialised"])
    model_instance = get_model(
        provider_id=job["provider_id"],
        model_id=job["model_id"],
        api_key=job["api_key"],
        base_url=job["base_url"]
    )

    if job["provider_id"] == "google_gemini":
        job["history_messages"] = history_messages
        stream_with_native_google_sdk(conn, job)
        return

    if not model_instance:
        conn.send(f"{ERROR_PREFIX}Failed to initialize model for provider '{job['provider_id']}'.")
        return

    chain = get_chain(model_instance, history_messages)
    for chunk in chain.stream({"input": job["prompt"]}):
        conn.send(chunk)


def start_worker(conn: Connection):
    """
    The main loop for the worker process. Waits for jobs and processes them.
    """
    print(f"[Worker Process - PID {os.getpid()}]: Started and waiting for jobs.")
    while True:
        try:
            job = conn.recv()
            if job == "EXIT":
                print("[Worker Process]: EXIT signal received. Shutting down.")
                break
            
            process_job(conn, job)

        except Exception as e:
            tb_str = traceback.format_exc()
            print(f"[Worker Process]: An unexpected error occurred: {e}\n{tb_str}")
            try:
                conn.send(f"{ERROR_PREFIX}An unexpected error occurred in the worker: {e}\n{tb_str}")
            except Exception as pipe_err:
                print(f"[Worker Process]: Failed to send error over pipe: {pipe_err}")
        finally:
            conn.send(STREAM_END_SIGNAL)
            print("[Worker Process]: Finished job and sent stream end signal.")
    
    print("[Worker Process]: Exiting.")