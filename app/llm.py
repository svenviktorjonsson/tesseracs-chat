import sys
import os
import traceback
from typing import List, Callable, Optional, Any
import asyncio
import sqlite3

from fastapi import WebSocket

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, Runnable, RunnableLambda
from langchain_core.messages import BaseMessage
from langchain_ollama.llms import OllamaLLM
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

from . import config
from . import state
from . import database
from . import utils
from . import encryption_utils
from . import project_utils

# --- System Prompt Definition ---
# app/llm.py

SYSTEM_PROMPT = (
    "You are a versatile and helpful AI assistant with expertise in programming and data visualization.\n\n"
    "## Core Directives:\n"
    "1.  **Standard Responses:** For general questions, facts, explanations, or single code snippets, provide the answer directly using clear text, Markdown, and fenced code blocks as appropriate. Use LaTeX for mathematics.\n"
    "2.  **Multi-File Projects:** When a user's request implies creating a multi-file project (e.g., 'make a website', 'create a flask app'), you MUST use the special project format.\n\n"
    "## Project Format Rules:\n"
    "1.  **Project Block:** Start the entire project with a `\\project{}` block. This block must contain the project's `name` and the command to `run` it.\n"
    "    - `name`: A descriptive name for the project (e.g., \"Simple Flask App\").\n"
    "    - `run`: The shell command required to execute the project (e.g., \"python app.py\" or \"npm install && npm start\").\n"
    "2.  **File Blocks:** Immediately following the `\\project{}` block, provide each file. Every file block MUST consist of:\n"
    "    a. A file path on a single line, ending with a colon (e.g., `./app/main.py:` or `./static/style.css:`).\n"
    "    b. A standard Markdown fenced code block with the language identifier (e.g., ```python).\n"
    "3.  **End Block:** Conclude the entire project definition with `\\endproject`.\n"
    "4.  **Best Practices:** Always use logical and conventional file and folder structures for the type of project requested.\n\n"
    "## Example Project:\n"
    "\\project{name: \"My First Website\", run: \"python -m http.server 8000\"}\n"
    "./index.html:\n"
    "```html\n"
    "<!DOCTYPE html><html><body><h1>Hello!</h1></body></html>\n"
    "```\n"
    "./style.css:\n"
    "```css\n"
    "h1 { color: blue; }\n"
    "```\n"
    "\\endproject\n\n"
    "## Other Rules:\n"
    "-   Always use `matplotlib.pyplot` for plots in Python code blocks.\n"
    "-   NEVER wrap code in one language inside another (e.g., do not write Python to print HTML).\n"
    "-   For complex requests not involving projects, you may explain your reasoning using the `\\think` command."
)

def get_model(
    provider_id: str,
    model_id: str,
    api_key: Optional[str] = None,
    base_url_override: Optional[str] = None
) -> Optional[Any]:
    print(f"LLM: Attempting to get model for provider='{provider_id}', model='{model_id}', base_url_override='{base_url_override}'")
    provider_config = config.get_provider_config(provider_id)

    if not provider_config:
        print(f"LLM_ERROR: Provider ID '{provider_id}' not found in LLM_PROVIDERS configuration.")
        return None

    provider_type = provider_config.get("type")
    
    final_base_url: Optional[str] = None
    if base_url_override and base_url_override.strip():
        final_base_url = base_url_override.strip()
    else:
        final_base_url = provider_config.get("base_url")

    if provider_type in ["openai_compatible"] and not final_base_url:
        print(f"LLM_ERROR: Final base URL for provider '{provider_id}' (type: {provider_type}) is missing.")
        return None

    resolved_api_key = api_key
    api_key_env_name = provider_config.get("api_key_env_var_name")

    if not resolved_api_key and api_key_env_name:
        resolved_api_key = os.getenv(api_key_env_name)
    
    try:
        if provider_type == "openai_compatible":
            if api_key_env_name and not resolved_api_key:
                print(f"LLM_WARNING: API key for openai_compatible provider '{provider_id}' not found. Proceeding without if possible.")
            return ChatOpenAI(
                model_name=model_id,
                openai_api_base=final_base_url,
                openai_api_key=resolved_api_key,
            )
        
        elif provider_type == "google_genai":
            if not resolved_api_key:
                print(f"LLM_ERROR: Google API Key is required for provider '{provider_id}' but was not provided.")
                return None
            return ChatGoogleGenerativeAI(
                model=model_id,
                google_api_key=resolved_api_key
            )

        elif provider_type == "anthropic":
            if not resolved_api_key:
                print(f"LLM_ERROR: Anthropic API Key is required for provider '{provider_id}' but was not provided.")
                return None
            return ChatAnthropic(
                    model=model_id,
                    anthropic_api_key=resolved_api_key,
                    max_tokens=8192,
                )
            
        else:
            print(f"LLM_ERROR: Unknown provider type '{provider_type}' for provider ID '{provider_id}'.")
            return None

    except Exception as e:
        print(f"LLM_CRITICAL_ERROR: Failed to initialize model for provider '{provider_id}', model '{model_id}': {e}")
        traceback.print_exc()
        return None

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])

output_parser = StrOutputParser()

def create_chain(
    provider_id: str,
    model_id: str,
    memory_loader_func: Callable[[dict], List[BaseMessage]],
    api_key: Optional[str] = None,
    base_url_override: Optional[str] = None
) -> Optional[Runnable]:
    try:
        current_model_instance = get_model(
            provider_id=provider_id,
            model_id=model_id,
            api_key=api_key,
            base_url_override=base_url_override
        )
        if not current_model_instance:
            print(f"LLM Chain ERROR: Failed to get model instance for provider '{provider_id}', model '{model_id}'. Cannot create chain.")
            return None

        chain = (
            RunnablePassthrough.assign(history=RunnableLambda(memory_loader_func))
            | prompt
            | current_model_instance
            | output_parser
        )
        print(f"LLM Chain: Successfully created chain for '{model_id}' from '{provider_id}'.")
        return chain
    except Exception as e:
        print(f"LLM Chain CRITICAL_ERROR: Failed to create LangChain chain: {e}")
        traceback.print_exc()
        return None

async def invoke_llm_for_session(
    session_id: str,
    websocket: WebSocket,
    user_id: int,
    user_input_raw: str,
    turn_id: int,
    stream_id: str,
    reply_to_message_id: Optional[int]
):
    stop_event = await state.register_ai_stream(stream_id)
    full_response_content = ""
    db_conn = None
    ai_message_id = None
    
    try:
        db_conn_for_settings = database.get_db_connection()
        cursor = db_conn_for_settings.cursor()
        cursor.execute(
            "SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?",
            (user_id,)
        )
        user_settings = cursor.fetchone()
        db_conn_for_settings.close()

        provider_id = user_settings["selected_llm_provider_id"] if user_settings else None
        model_id = user_settings["selected_llm_model_id"] if user_settings else None

        if not provider_id or not model_id:
            await websocket.send_text("<ERROR> AI provider not configured. Please select a provider and model in your User Settings.")
            return 

        api_key = None
        base_url = None
        if user_settings:
            if user_settings["user_llm_api_key_encrypted"]:
                api_key = encryption_utils.decrypt_data(user_settings["user_llm_api_key_encrypted"])
            base_url = user_settings["selected_llm_base_url"]
        
        def get_history(input_dict: dict) -> List[BaseMessage]:
            memory = state.get_memory_for_client(session_id)
            return memory.chat_memory.messages

        chain = create_chain(
            provider_id=provider_id,
            model_id=model_id,
            memory_loader_func=get_history,
            api_key=api_key,
            base_url_override=base_url
        )

        if not chain:
            raise ValueError(f"Failed to create chain for provider {provider_id}")

        stream = chain.astream({"input": user_input_raw})

        async for chunk in stream:
            if stop_event.is_set():
                break
            
            full_response_content += chunk
            await utils.send_ws_message(websocket, "ai_chunk", chunk)
            await asyncio.sleep(0.01)

    except Exception as e:
        print(f"--- LLM ERROR for stream '{stream_id}' ---")
        traceback.print_exc()
        error_message = f"<ERROR> AI Error: {str(e)}"
        await websocket.send_text(error_message)
    finally:
        print(f"--- LLM STREAM: Finalizing stream '{stream_id}' ---")
        if full_response_content:
            try:
                db_conn = database.get_db_connection()
                cursor = db_conn.cursor()
                cursor.execute(
                    """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, turn_id, reply_to_message_id, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))""",
                    (session_id, user_id, 'AI', 'ai', full_response_content, turn_id, reply_to_message_id)
                )
                ai_message_id = cursor.lastrowid
                db_conn.commit()
                state.remove_memory_for_client(session_id)
            except sqlite3.Error as db_err:
                print(f"--- DB ERROR: Failed to save AI response for stream '{stream_id}': {db_err} ---")
                traceback.print_exc()
            finally:
                if db_conn:
                    db_conn.close()

        await utils.send_ws_message(websocket, "ai_stream_end", {"message_id": ai_message_id})
        await state.unregister_ai_stream(stream_id)
