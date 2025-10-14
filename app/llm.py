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

def _process_and_save_ai_response(session_id: str, user_id: int, full_raw_content: str, turn_id: int, reply_to_id: Optional[int]) -> Tuple[Optional[int], Optional[str]]:
    """Parses the full AI response, applies creation/edit/update logic, and saves the results."""
    conn = None
    try:
        outer_block_match = re.search(r"^_([A-Z_]+)_START_", full_raw_content)
        block_type = outer_block_match.group(1) if outer_block_match else "ANSWER"

        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        new_message_content = full_raw_content
        new_project_id = None
        original_message_id_to_link = None
        link_text = ""

        if block_type in ["EDIT_ANSWER", "UPDATE_ANSWER"]:
            payload_match = re.search(r"(\{.*?\})_JSON_END_", new_message_content, re.DOTALL)
            payload = json.loads(payload_match.group(1)) if payload_match else {}
            content_after_json = new_message_content[payload_match.end():].rsplit('_', 2)[0]
            new_message_content = _apply_answer_modifications(cursor, block_type, payload, content_after_json)
            original_message_id_to_link = payload.get("answer_to_edit_id") or payload.get("answer_to_update_id")
            link_text = "This answer has been updated. Click here to see the new version."

        elif block_type in ["EDIT_PROJECT", "UPDATE_PROJECT"]:
            payload_match = re.search(r"(\{.*?\})_JSON_END_", new_message_content, re.DOTALL)
            payload = json.loads(payload_match.group(1)) if payload_match else {}
            
            project_id_to_edit = payload.get("project_to_edit_id")
            cursor.execute("SELECT id FROM chat_messages WHERE project_id = ? ORDER BY timestamp DESC LIMIT 1", (project_id_to_edit,))
            original_msg_row = cursor.fetchone()
            original_message_id_to_link = original_msg_row['id'] if original_msg_row else None
            
            cursor.execute("SELECT git_repo_blob, name FROM projects WHERE id = ?", (project_id_to_edit,))
            original_proj_row = cursor.fetchone()

            if original_proj_row:
                parsed_data = project_utils.parse_project_modification_blocks(new_message_content)
                new_repo_blob, error = project_utils.apply_project_modifications(
                    repo_blob=original_proj_row['git_repo_blob'],
                    operations=parsed_data['operations'],
                    commit_message=payload.get("commit_message", "AI modification"),
                    user_name="AI Assistant"
                )
                if new_repo_blob and not error:
                    new_project_id = str(uuid.uuid4())
                    cursor.execute("INSERT INTO projects (id, session_id, name, git_repo_blob) VALUES (?, ?, ?, ?)",
                                   (new_project_id, session_id, original_proj_row['name'], new_repo_blob))
                    link_text = "This project has been updated. Click here to see the new version."

        elif block_type == "PROJECT":
            project_data = project_utils.parse_project_from_full_response(full_raw_content)
            
            if project_data:
                repo_blob, error = project_utils.create_and_pack_git_repo(project_data)
                
                if repo_blob and not error:
                    new_project_id = str(uuid.uuid4())
                    cursor.execute("INSERT INTO projects (id, session_id, name, git_repo_blob) VALUES (?, ?, ?, ?)",
                                   (new_project_id, session_id, project_data.get("name"), repo_blob))
                else:
                    print(f"[LLM_SAVE_ERROR] Failed to create git repo blob. Error: {error}")
            else:
                print("[LLM_SAVE_ERROR] Failed to parse project data from raw content.")

        cursor.execute(
            """INSERT INTO chat_messages (session_id, user_id, sender_name, sender_type, content, turn_id, reply_to_message_id, prompting_user_id, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, 'AI', 'ai', new_message_content, turn_id, reply_to_id, user_id, new_project_id)
        )
        new_message_id = cursor.lastrowid

        if original_message_id_to_link and new_message_id:
            _update_original_message_to_link(cursor, original_message_id_to_link, new_message_id, link_text)

        conn.commit()
        return new_message_id, new_project_id

    except Exception as e:
        print(f"!!! LLM_SAVE_ERROR: An exception occurred in _process_and_save_ai_response: {e}")
        traceback.print_exc()
        if conn: conn.rollback()
        return None, None
    finally:
        if conn: conn.close()

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
            "prompt": user_input_raw, "provider_id": provider_id,
            "model_id": settings.get("selected_llm_model_id"), "api_key": settings.get("api_key"),
            "base_url": settings.get("selected_llm_base_url"),
            "history_messages_serialised": messages_to_dict(memory.chat_memory.messages)
        }

        await loop.run_in_executor(None, parent_conn.send, job_payload)
        
        await state.broadcast(session_id, {"type": "ai_thinking", "payload": {"turn_id": turn_id, "prompting_user_id": user_id, "prompting_user_name": user_name}})
        await asyncio.sleep(0)

        parser_stack = []
        buffer = ""
        tag_regex = re.compile(r"(_(ANSWER|PROJECT|FILE|EDIT_ANSWER|UPDATE_ANSWER|EDIT_PROJECT|UPDATE_PROJECT|EDIT_FILE|UPDATE_FILE|EXTEND_FILE)_(START|END)_)")
        json_end_tag = "_JSON_END_"
        MAX_TAG_LENGTH = 40 

        while not stop_event.is_set():
            chunk = await queue.get()
            if chunk == STREAM_END_SIGNAL: break
            if chunk.startswith(ERROR_PREFIX):
                all_content_parts.append(chunk)
                await state.broadcast(session_id, {"type": "error", "payload": chunk})
                break

            all_content_parts.append(chunk)
            buffer += chunk

            while True:
                match = tag_regex.search(buffer)
                if not match:
                    if parser_stack and len(buffer) > MAX_TAG_LENGTH:
                        split_pos = len(buffer) - MAX_TAG_LENGTH
                        content_chunk = buffer[:split_pos]
                        buffer = buffer[split_pos:]
                        
                        current_state = parser_stack[-1]
                        if current_state in ["ANSWER", "UPDATE_ANSWER", "PROJECT", "UPDATE_PROJECT"]:
                            await state.broadcast(session_id, {"type": "ai_chunk", "payload": content_chunk}); await asyncio.sleep(0)
                        elif current_state in ["FILE", "UPDATE_FILE", "EXTEND_FILE"]:
                            await state.broadcast(session_id, {"type": "file_chunk", "payload": {"content": content_chunk}}); await asyncio.sleep(0)
                    break 

                content_before_tag = buffer[:match.start()]
                if content_before_tag:
                    current_state_before = parser_stack[-1] if parser_stack else None
                    if current_state_before in ["ANSWER", "UPDATE_ANSWER", "PROJECT", "UPDATE_PROJECT"]:
                        await state.broadcast(session_id, {"type": "ai_chunk", "payload": content_before_tag}); await asyncio.sleep(0)
                    elif current_state_before in ["FILE", "UPDATE_FILE", "EXTEND_FILE"]:
                        await state.broadcast(session_id, {"type": "file_chunk", "payload": {"content": content_before_tag}}); await asyncio.sleep(0)
                
                buffer = buffer[match.end():]
                
                # --- START OF THE CRITICAL FIX ---
                # The regex has 3 groups, so we unpack 3 values.
                tag_full, tag_type, tag_action = match.groups()
                # --- END OF THE CRITICAL FIX ---

                if tag_action == "START":
                    if json_end_tag in buffer:
                        json_str, rest_of_buffer = buffer.split(json_end_tag, 1)
                        buffer = rest_of_buffer
                        try:
                            args = json.loads(json_str.strip())
                            parser_stack.append(tag_type)
                            
                            event_payload = {**args, 'turn_id': turn_id, 'prompting_user_id': user_id, 'prompting_user_name': user_name}
                            if 'path' in event_payload:
                                event_payload['language'] = project_utils.get_language_from_extension(event_payload['path'])
                            
                            event_map = {
                                "PROJECT": "project_header", "FILE": "start_file_stream",
                                "UPDATE_PROJECT": "project_update_start", "EDIT_PROJECT": "project_edit_start",
                                "UPDATE_ANSWER": "answer_update_start", "EDIT_ANSWER": "answer_edit_start",
                                "UPDATE_FILE": "file_update_start", "EXTEND_FILE": "file_extend_start", "EDIT_FILE": "file_edit_start"
                            }
                            if tag_type in event_map:
                                await state.broadcast(session_id, {"type": event_map[tag_type], "payload": event_payload}); await asyncio.sleep(0)
                        except json.JSONDecodeError:
                            print(f"PARSER WARNING: Invalid JSON in stream for {tag_type}: {json_str}")
                            buffer = tag_full + json_str + json_end_tag + rest_of_buffer
                    else:
                        buffer = tag_full + buffer
                        break
                
                elif tag_action == "END":
                    if parser_stack and parser_stack[-1] == tag_type:
                        parser_stack.pop()
                        end_event_map = {
                            "ANSWER": "end_answer_stream", "FILE": "end_file_stream", "UPDATE_ANSWER": "end_answer_update", 
                            "UPDATE_PROJECT": "end_project_update", "EDIT_PROJECT": "end_project_edit", 
                            "UPDATE_FILE": "end_file_update", "EXTEND_FILE": "end_file_extend"
                        }
                        if tag_type in end_event_map:
                            await state.broadcast(session_id, {"type": end_event_map[tag_type], "payload": {"turn_id": turn_id}}); await asyncio.sleep(0)
                    else:
                        print(f"PARSER WARNING: Mismatched end tag. Stack: {parser_stack}, Got: {tag_type}")

        if buffer and parser_stack:
            current_state = parser_stack[-1]
            if current_state in ["ANSWER", "UPDATE_ANSWER", "PROJECT", "UPDATE_PROJECT"]:
                await state.broadcast(session_id, {"type": "ai_chunk", "payload": buffer})
            elif current_state in ["FILE", "UPDATE_FILE", "EXTEND_FILE"]:
                await state.broadcast(session_id, {"type": "file_chunk", "payload": {"content": buffer}}); await asyncio.sleep(0)

    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"LLM_SYSTEM ERROR in invoke_llm_for_session: {e}\n{tb_str}")
        await state.broadcast(session_id, {"type": "error", "payload": f"AI Error: {str(e)}"})
    finally:
        loop.remove_reader(pipe_fileno)

        if stop_event.is_set():
            print(f"LLM_SYSTEM: Stream {stream_id} was stopped by client.")
        
        full_response_content = "".join(all_content_parts)
        
        ai_message_id, saved_project_id = await asyncio.to_thread(
            _process_and_save_ai_response,
            session_id, user_id, full_response_content, turn_id, reply_to_id
        )
        
        if ai_message_id is not None:
            state.remove_memory_for_client(session_id)
        
        await state.broadcast(session_id, {"type": "ai_stream_end", "payload": {"message_id": ai_message_id, "turn_id": turn_id, "project_id": saved_project_id}})
        await state.unregister_ai_stream(stream_id)

def _update_original_message_to_link(cursor: sqlite3.Cursor, original_message_id: int, new_message_id: int, text: str):
    """Updates an existing message to become a link to a new message."""
    link_content = json.dumps({
        "type": "link",
        "target_message_id": new_message_id,
        "text": text
    })
    # We also nullify the project_id from the original message as it's now linked to the new one
    cursor.execute(
        "UPDATE chat_messages SET content = ?, project_id = NULL WHERE id = ?",
        (link_content, original_message_id)
    )
    
def _apply_answer_modifications(cursor: sqlite3.Cursor, block_type: str, payload: dict, content: str) -> Optional[str]:
    """Applies modifications to an answer's content and returns the new content."""
    answer_id_key = "answer_to_edit_id" if block_type == "EDIT_ANSWER" else "answer_to_update_id"
    original_message_id = payload.get(answer_id_key)
    if not original_message_id:
        return None

    if block_type == "UPDATE_ANSWER":
        return content # For UPDATE, the streamed content is the new content.

    elif block_type == "EDIT_ANSWER":
        cursor.execute("SELECT content FROM chat_messages WHERE id = ?", (original_message_id,))
        original_row = cursor.fetchone()
        if not original_row:
            return None
        
        original_content = original_row['content']
        try:
            modifications = json.loads(content.strip())
            for mod in modifications:
                original_content = re.sub(mod['find'], mod['replace'], original_content)
            return original_content
        except (json.JSONDecodeError, TypeError):
            print(f"LLM_SAVE_ERROR: Could not apply answer edits due to invalid JSON. Content: {content}")
            return None
    return None