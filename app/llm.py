import os
import uuid
import traceback
import json
import re
import asyncio
import sqlite3
from multiprocessing import Process, Pipe
from multiprocessing.connection import Connection
from typing import List, Optional, Any, Dict, Tuple

from fastapi import WebSocket
from langchain_core.messages import messages_to_dict

from . import state, database, encryption_utils, project_utils, config
from .llm_worker import start_worker, STREAM_END_SIGNAL, ERROR_PREFIX

# --- Global variables for the worker process and communication pipe ---
worker_process: Optional[Process] = None
parent_conn: Optional[Connection] = None

def start_llm_worker():
    """
    Starts the background worker process and establishes a pipe for communication.
    This should be called once when the main application starts up.
    """
    global worker_process, parent_conn
    if worker_process is None or not worker_process.is_alive():
        print("LLM_SYSTEM: Starting LLM worker process...")
        parent_conn, child_conn = Pipe()
        worker_process = Process(target=start_worker, args=(child_conn,))
        worker_process.start()
        child_conn.close()
        print(f"LLM_SYSTEM: Worker process started with PID {worker_process.pid}.")

def shutdown_llm_worker():
    """
    Sends an EXIT signal to the worker and terminates it.
    This should be called when the main application shuts down.
    """
    global worker_process, parent_conn
    print("LLM_SYSTEM: Shutting down LLM worker process...")
    if parent_conn:
        try:
            parent_conn.send("EXIT")
        except (BrokenPipeError, EOFError):
            print("LLM_SYSTEM: Pipe to worker was already closed.")
    if worker_process and worker_process.is_alive():
        worker_process.join(timeout=5)
        if worker_process.is_alive():
            print("LLM_SYSTEM: Worker did not exit gracefully, terminating.")
            worker_process.terminate()
            worker_process.join()
    print("LLM_SYSTEM: Worker process shut down.")

# --- Database and Settings Functions (Copied from original, unchanged) ---
def _get_llm_settings_sync(user_id: int) -> dict:
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT selected_llm_provider_id, selected_llm_model_id, user_llm_api_key_encrypted, selected_llm_base_url FROM users WHERE id = ?",
            (user_id,)
        )
        user_settings = cursor.fetchone()
        if not user_settings:
            return {"error": "User settings not found."}
        settings = dict(user_settings)
        api_key_encrypted = settings.get("user_llm_api_key_encrypted")
        settings["api_key"] = encryption_utils.decrypt_data(api_key_encrypted) if api_key_encrypted else None
        return settings
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if conn:
            conn.close()

def _save_ai_message_sync(session_id: str, user_id: int, content: Optional[str], turn_id: int, reply_to_id: Optional[int], project_data: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[str]]:
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        project_id_to_save = None
        if project_data:
            repo_blob, error = project_utils.create_and_pack_git_repo(project_data)
            if error:
                print(f"LLM_SAVE_ERROR: Could not pack git repo: {error}")
            elif repo_blob:
                project_id_to_save = str(uuid.uuid4())
                project_name = project_data.get("name", "Untitled Project")
                cursor.execute(
                    """INSERT INTO projects (id, session_id, name, git_repo_blob)
                       VALUES (?, ?, ?, ?)""",
                    (project_id_to_save, session_id, project_name, repo_blob)
                )

        cursor.execute(
            """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, turn_id, reply_to_message_id, prompting_user_id, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, 'AI', 'ai', content or None, turn_id, reply_to_id, user_id, project_id_to_save)
        )
        
        message_id = cursor.lastrowid
        conn.commit()
        return message_id, project_id_to_save

    except sqlite3.Error as db_err:
        traceback.print_exc()
        if conn:
            conn.rollback()
        return None, None
    finally:
        if conn:
            conn.close()
async def invoke_llm_for_session(
    session_id: str,
    websocket: WebSocket,
    user_id: int,
    user_name: str,
    user_input_raw: str,
    turn_id: int,
    stream_id: str,
    reply_to_message_id: Optional[int]
):
    loop = asyncio.get_running_loop()
    all_content_parts = []
    all_files = []
    project_name = "Untitled Project"
    ai_message_id = None
    stop_event = await state.register_ai_stream(stream_id)
    queue = asyncio.Queue()

    reply_to_id = reply_to_message_id

    def pipe_data_received():
        try:
            data = parent_conn.recv()
            queue.put_nowait(data)
        except Exception as e:
            print(f"Error reading from pipe: {e}")
            queue.put_nowait(STREAM_END_SIGNAL)

    pipe_fileno = parent_conn.fileno()
    loop.add_reader(pipe_fileno, pipe_data_received)

    try:
        if not parent_conn or not worker_process or not worker_process.is_alive():
            await state.broadcast(session_id, {"type": "error", "payload": "LLM worker process is not running."}); await asyncio.sleep(0)
            return

        settings = await asyncio.to_thread(_get_llm_settings_sync, user_id)
        if "error" in settings:
            await state.broadcast(session_id, {"type": "error", "payload": f"Could not load LLM settings: {settings['error']}"}); await asyncio.sleep(0)
            return
        
        provider_id = settings.get("selected_llm_provider_id")
        if not provider_id or not settings.get("selected_llm_model_id"):
            await state.broadcast(session_id, {"type": "error", "payload": "AI provider not configured."}); await asyncio.sleep(0)
            return

        memory = await state.get_memory_for_client(session_id)
        
        job_payload = {
            "prompt": user_input_raw,
            "provider_id": provider_id,
            "model_id": settings.get("selected_llm_model_id"),
            "api_key": settings.get("api_key"),
            "base_url": settings.get("selected_llm_base_url"),
            "history_messages_serialised": messages_to_dict(memory.chat_memory.messages)
        }

        await loop.run_in_executor(None, parent_conn.send, job_payload)
        
        parser_stack = []
        buffer = ""
        tag_regex = re.compile(r"(_(ANSWER|PROJECT|FILE)_(START|END)_)")
        json_end_tag = "_JSON_END_"
        MAX_TAG_LENGTH = 20
        await state.broadcast(session_id, {"type": "ai_thinking", "payload": {"turn_id": turn_id, "prompting_user_id": user_id, "prompting_user_name": user_name}})
        await asyncio.sleep(0)

        while not stop_event.is_set():
            chunk = await queue.get()
            
            if chunk == STREAM_END_SIGNAL:
                break
            
            if chunk.startswith(ERROR_PREFIX):
                all_content_parts.append(chunk)
                break
            
            buffer += chunk

            while True:
                match = tag_regex.search(buffer)

                if not match:
                    if parser_stack and len(buffer) > MAX_TAG_LENGTH:
                        split_pos = len(buffer) - MAX_TAG_LENGTH
                        content_chunk = buffer[:split_pos]
                        buffer = buffer[split_pos:]

                        current_state = parser_stack[-1]
                        if current_state in ["ANSWER", "PROJECT"]:
                            all_content_parts.append(content_chunk)
                            await state.broadcast(session_id, {"type": "ai_chunk", "payload": content_chunk}); await asyncio.sleep(0)
                        elif current_state == "FILE":
                            if all_files: all_files[-1]["content"] += content_chunk
                            await state.broadcast(session_id, {"type": "file_chunk", "payload": {"content": content_chunk}}); await asyncio.sleep(0)
                    break

                content_before_tag = buffer[:match.start()]
                if content_before_tag:
                    current_state = parser_stack[-1] if parser_stack else None
                    if current_state in ["ANSWER", "PROJECT"]:
                        all_content_parts.append(content_before_tag)
                        await state.broadcast(session_id, {"type": "ai_chunk", "payload": content_before_tag}); await asyncio.sleep(0)
                    elif current_state == "FILE":
                        if all_files: all_files[-1]["content"] += content_before_tag
                        await state.broadcast(session_id, {"type": "file_chunk", "payload": {"content": content_before_tag}}); await asyncio.sleep(0)

                buffer = buffer[match.end():]
                tag_full = match.group(0)
                tag_type = match.group(2)
                tag_action = match.group(3)
                
                if tag_action == "START":
                    if json_end_tag in buffer:
                        json_str, rest_of_buffer = buffer.split(json_end_tag, 1)
                        buffer = rest_of_buffer.lstrip()
                        try:
                            args = json.loads(json_str.strip())
                        except json.JSONDecodeError:
                            args = {}
                        
                        parser_stack.append(tag_type)
                        if tag_type == "PROJECT":
                            project_name = args.get("name", "Untitled Project")
                            await state.broadcast(session_id, {"type": "project_header", "payload": {"name": project_name, "turn_id": turn_id, "prompting_user_id": user_id, "prompting_user_name": user_name}}); await asyncio.sleep(0)
                        elif tag_type == "FILE":
                            path = args.get("path", "untitled")
                            lang = project_utils.get_language_from_extension(path)
                            all_files.append({"path": path, "content": "", "language": lang})
                            args["language"] = lang
                            await state.broadcast(session_id, {"type": "start_file_stream", "payload": args}); await asyncio.sleep(0)
                    else:
                        buffer = tag_full + buffer
                        break
                elif tag_action == "END":
                    if parser_stack and parser_stack[-1] == tag_type:
                        parser_stack.pop()
                        if tag_type == "FILE":
                            await state.broadcast(session_id, {"type": "end_file_stream", "payload": {}}); await asyncio.sleep(0)
                        elif tag_type == "ANSWER":
                            await state.broadcast(session_id, {"type": "end_answer_stream", "payload": {"turn_id": turn_id}}); await asyncio.sleep(0)
                    else:
                        print(f"PARSER WARNING: Mismatched end tag. Stack: {parser_stack}, Got: {tag_type}")
        
        if buffer and parser_stack:
            current_state = parser_stack[-1]
            if current_state in ["ANSWER", "PROJECT"]:
                all_content_parts.append(buffer)
                await state.broadcast(session_id, {"type": "ai_chunk", "payload": buffer})
            elif current_state == "FILE":
                if all_files: all_files[-1]["content"] += buffer
                await state.broadcast(session_id, {"type": "file_chunk", "payload": {"content": buffer}})

    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"LLM_SYSTEM ERROR in invoke_llm_for_session: {e}\n{tb_str}")
        await state.broadcast(session_id, {"type": "error", "payload": f"AI Error: {str(e)}"})
    finally:
        loop.remove_reader(pipe_fileno)

        if stop_event.is_set():
            print(f"LLM_SYSTEM: Stream {stream_id} was stopped by client.")
        
        full_response_content = "".join(all_content_parts)
        
        project_data_to_save = None
        if all_files:
            project_data_to_save = {
                "name": project_name,
                "files": all_files
            }

        saved_project_id = None
        if full_response_content or project_data_to_save:
            ai_message_id, saved_project_id = await asyncio.to_thread(
                _save_ai_message_sync,
                session_id, user_id, full_response_content, turn_id, reply_to_id, project_data_to_save
            )
            if ai_message_id is not None:
                state.remove_memory_for_client(session_id)
        
        await state.broadcast(session_id, {"type": "ai_stream_end", "payload": {"message_id": ai_message_id, "turn_id": turn_id, "project_id": saved_project_id}})
        await state.unregister_ai_stream(stream_id)