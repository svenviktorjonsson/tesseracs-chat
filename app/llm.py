import sys
import os
import traceback
from typing import List, Callable, Optional, Any
import asyncio
import sqlite3
from pathlib import Path
import re
import json
import uuid

from fastapi import WebSocket

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, Runnable, RunnableLambda
from langchain_core.messages import BaseMessage, SystemMessage
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

SYSTEM_PROMPT = (
    "You are a helpful AI assistant. Follow the user's instructions carefully.\n\n"
    "RESPONSE FORMATTING RULES:\n"
    "Your entire output must be a sequence of one or more structured blocks. Each block follows the same simple format: a START tag, a JSON payload terminated by _JSON_END_, content, and an END tag.\n\n"
    "--- BLOCK TYPES ---\n\n"
    "1.  **Answer Block:** For conversational text, Markdown, and math.\n"
    "    - Use `_ANSWER_START_` and `_ANSWER_END_`.\n"
    "    - The JSON payload must be empty: `{}`.\n"
    "    - All text must be formatted with Markdown. All math must use KaTeX syntax (`$$...$$` or `$...$`).\n\n"
    "2.  **File Block:** For all code or file content.\n"
    "    - A 'project' is simply a sequence of one or more file blocks.\n"
    "    - Use `_FILE_START_` and `_FILE_END_`.\n"
    "    - The JSON payload must contain the file path: `{ \"path\": \"./path/to/file.ext\" }`.\n"
    "    - The **last file block** in a code project MUST be the `run.sh` script.\n"
    "    - For Python projects, `run.sh` must use `uv pip install` for dependencies.\n\n"
    "--- EXAMPLES ---\n\n"
    "**EXAMPLE 1: Conversational Answer with Math**\n"
    "_ANSWER_START_\n"
    "{}\n"
    "_JSON_END_\n"
    "The quadratic formula is used to solve equations of the form $ax^2 + bx + c = 0$. The formula is:\n\n"
    "$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$\n"
    "_ANSWER_END_\n\n"
    "**EXAMPLE 2: Code Project**\n"
    "_FILE_START_\n"
    "{ \"path\": \"./main.py\" }\n"
    "_JSON_END_\n"
    "import numpy as np\n"
    "print(f'Numpy version: {np.__version__}!')\n"
    "_FILE_END_\n"
    "_FILE_START_\n"
    "{ \"path\": \"./run.sh\" }\n"
    "_JSON_END_\n"
    "uv pip install numpy\n"
    "python main.py\n"
    "_FILE_END_"
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
    SystemMessage(content=SYSTEM_PROMPT),
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
    ai_message_id = None
    project_files = []
    answer_content = "" # New variable to hold just the conversational text

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
            await websocket.send_text("<ERROR> AI provider not configured.")
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
            provider_id=provider_id, model_id=model_id,
            memory_loader_func=get_history, api_key=api_key, base_url_override=base_url
        )

        if not chain:
            raise ValueError(f"Failed to create chain for provider {provider_id}")

        stream = chain.astream({"input": user_input_raw})
        
        parser_state = "IDLE"
        buffer = ""
        current_block_type = None
        current_file_args = {}
        current_file_content = ""

        print("\n--- LLM RAW STREAM START ---", flush=True)
        async for chunk in stream:
            print(chunk, end="", flush=True)
            if stop_event.is_set():
                break
            
            full_response_content += chunk
            buffer += chunk

            while True:
                can_process_more = False
                if parser_state == "IDLE":
                    match = re.search(r"(_ANSWER_START_|_FILE_START_)", buffer)
                    if match:
                        current_block_type = match.group(1)
                        buffer = buffer[match.end():]
                        parser_state = "PARSING_ARGS"
                        can_process_more = True

                elif parser_state == "PARSING_ARGS":
                    end_json_match = re.search(r"(.*?)\s*_JSON_END_", buffer, re.DOTALL)
                    if end_json_match:
                        json_str = end_json_match.group(1).strip()
                        buffer = buffer[end_json_match.end():]
                        
                        try:
                            args = json.loads(json_str)
                            if current_block_type == "_ANSWER_START_":
                                await utils.send_ws_message(websocket, "start_answer_stream", {})
                                parser_state = "STREAMING_ANSWER"
                            elif current_block_type == "_FILE_START_":
                                current_file_args = args
                                path = current_file_args.get("path")
                                if path:
                                    current_file_args["language"] = project_utils.get_language_from_extension(path)
                                await utils.send_ws_message(websocket, "start_file_stream", current_file_args)
                                parser_state = "STREAMING_FILE_CONTENT"
                        except json.JSONDecodeError:
                            parser_state = "IDLE"
                        can_process_more = True

                elif parser_state == "STREAMING_ANSWER":
                    end_match = buffer.find("_ANSWER_END_")
                    if end_match != -1:
                        payload = buffer[:end_match]
                        if payload:
                            await utils.send_ws_message(websocket, "ai_chunk", payload)
                            answer_content += payload # Accumulate answer content
                        await utils.send_ws_message(websocket, "end_answer_stream", {})
                        buffer = buffer[end_match + len("_ANSWER_END_"):]
                        parser_state = "IDLE"
                        can_process_more = True
                    else:
                        if buffer:
                            await utils.send_ws_message(websocket, "ai_chunk", buffer)
                            answer_content += buffer # Accumulate answer content
                        buffer = ""
                
                elif parser_state == "STREAMING_FILE_CONTENT":
                    end_match = buffer.find("_FILE_END_")
                    if end_match != -1:
                        payload = buffer[:end_match]
                        if payload:
                             await utils.send_ws_message(websocket, "file_chunk", {"content": payload})
                        
                        current_file_content += payload
                        path = current_file_args.get("path")
                        if path:
                            project_files.append({
                                "path": path,
                                "content": current_file_content,
                                "language": current_file_args.get("language")
                            })
                        
                        await utils.send_ws_message(websocket, "end_file_stream", current_file_args)
                        buffer = buffer[end_match + len("_FILE_END_"):]
                        current_file_args, current_file_content = {}, ""
                        parser_state = "IDLE"
                        can_process_more = True
                    else:
                        if buffer:
                            await utils.send_ws_message(websocket, "file_chunk", {"content": buffer})
                        current_file_content += buffer
                        buffer = ""

                if not can_process_more:
                    break
        print("\n--- LLM RAW STREAM END ---", flush=True)
        
    except Exception as e:
        print(f"--- LLM ERROR for stream '{stream_id}' ---")
        traceback.print_exc()
        error_message = f"<ERROR> AI Error: {str(e)}"
        await websocket.send_text(error_message)
    finally:
        print(f"--- LLM STREAM: Finalizing stream '{stream_id}' ---")
        
        # --- NEW DATABASE SAVING LOGIC ---
        ai_message_id = None
        if full_response_content: 
            try:
                db_conn = database.get_db_connection()
                cursor = db_conn.cursor()
                
                # Step 1: Insert the main message with only conversational content
                cursor.execute(
                    """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, turn_id, reply_to_message_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, user_id, 'AI', 'ai', answer_content or None, turn_id, reply_to_message_id)
                )
                ai_message_id = cursor.lastrowid

                # Step 2: If there are files, insert them and link them to the new message ID
                if project_files:
                    for file_data in project_files:
                        cursor.execute(
                            """INSERT INTO message_files (message_id, path, content, language)
                               VALUES (?, ?, ?, ?)""",
                            (ai_message_id, file_data['path'], file_data['content'], file_data['language'])
                        )
                
                db_conn.commit()
                state.remove_memory_for_client(session_id)
            except sqlite3.Error as db_err:
                print(f"--- DB ERROR: Failed to save structured AI response for stream '{stream_id}': {db_err} ---")
                traceback.print_exc()
            finally:
                if db_conn:
                    db_conn.close()

        print(f"--- LLM LOG: Finalizing stream '{stream_id}'. Sending ai_stream_end message. ---")
        await utils.send_ws_message(websocket, "ai_stream_end", {"message_id": ai_message_id, "turn_id": turn_id})
        await state.unregister_ai_stream(stream_id)