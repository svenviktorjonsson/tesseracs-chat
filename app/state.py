import asyncio
import json
import sqlite3
import datetime
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, messages_from_dict, messages_to_dict
from typing import Dict, Any, Optional, List
from fastapi import WebSocket
from . import database, project_utils
import traceback
import os
import subprocess
import shutil
from pathlib import Path

client_memory: Dict[str, ConversationBufferMemory] = {}
running_containers: Dict[str, Dict[str, Any]] = {}
running_containers_lock = asyncio.Lock()
active_ai_streams: Dict[str, asyncio.Event] = {}
active_ai_streams_lock = asyncio.Lock()
running_previews: Dict[str, Dict[str, Any]] = {}
running_previews_lock = asyncio.Lock()
preview_routes: Dict[str, str] = {}
running_code_tasks: Dict[str, asyncio.Task] = {}
running_code_tasks_lock = asyncio.Lock()

def _get_memory_from_db_sync(session_id: str) -> Optional[ConversationBufferMemory]:
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        )
        messages_rows = cursor.fetchall()
        
        langchain_messages: List[BaseMessage] = []

        for row in messages_rows:
            msg = dict(row)
            
            if msg['sender_type'] == 'user':
                langchain_messages.append(HumanMessage(content=msg['content'] or ""))
            
            elif msg['sender_type'] == 'ai':
                ai_content = msg['content'] or ""
                
                if msg['project_id']:
                    cursor.execute("SELECT git_repo_blob FROM projects WHERE id = ?", (msg['project_id'],))
                    project_row = cursor.fetchone()
                    
                    if project_row and project_row['git_repo_blob']:
                        repo_blob = project_row['git_repo_blob']
                        project_path, error = project_utils.unpack_git_repo_to_temp_dir(repo_blob)
                        
                        if project_path:
                            try:
                                log_result = subprocess.run(
                                    ['git', 'log', '--pretty=format:%h - %s (%cr)'],
                                    cwd=project_path, capture_output=True, text=True, check=True
                                )
                                commit_history = log_result.stdout.strip()

                                ls_result = subprocess.run(
                                    ['git', 'ls-tree', '-r', '--name-only', 'HEAD'],
                                    cwd=project_path, capture_output=True, text=True, check=True
                                )
                                file_paths = ls_result.stdout.strip().split('\n')

                                project_context_parts = [
                                    ai_content,
                                    "\n\n--- PROJECT CONTEXT ---",
                                    "Commit History:",
                                    commit_history,
                                    "\nLatest Files:"
                                ]

                                for file_path in file_paths:
                                    if file_path:
                                        try:
                                            full_file_path = os.path.join(project_path, file_path)
                                            file_content = Path(full_file_path).read_text(encoding="utf-8")
                                            project_context_parts.append(f"--- file: {file_path} ---\n{file_content}")
                                        except Exception as e:
                                            project_context_parts.append(f"--- file: {file_path} ---\nError reading file: {e}")
                                
                                ai_content = "\n".join(project_context_parts)

                            except Exception as e:
                                print(f"STATE_ERROR: Failed to process git repo for project {msg['project_id']}: {e}")
                            finally:
                                unpack_dir = os.path.dirname(project_path)
                                if os.path.exists(unpack_dir):
                                    shutil.rmtree(unpack_dir)
                
                langchain_messages.append(AIMessage(content=ai_content))

        if langchain_messages:
            memory_instance = ConversationBufferMemory(return_messages=True, memory_key="history")
            memory_instance.chat_memory.messages = langchain_messages
            return memory_instance
            
        return None
    finally:
        if conn:
            conn.close()

async def get_memory_for_client(session_id: str) -> ConversationBufferMemory:
    global client_memory
    if session_id in client_memory:
        return client_memory[session_id]

    try:
        memory_instance = await asyncio.to_thread(_get_memory_from_db_sync, session_id)
        if memory_instance:
            client_memory[session_id] = memory_instance
            return memory_instance
    except Exception as e:
        print(f"STATE ERROR: Failed to load/reconstruct memory for session {session_id}: {e}")
        traceback.print_exc()

    new_memory = ConversationBufferMemory(return_messages=True, memory_key="history")
    client_memory[session_id] = new_memory
    return new_memory

def remove_memory_for_client(session_id: str):
    global client_memory
    if session_id in client_memory:
        del client_memory[session_id]
        print(f"STATE: Memory cache cleared for session {session_id} due to data change.")

async def register_ai_stream(stream_id: str) -> asyncio.Event:
    async with active_ai_streams_lock:
        if stream_id in active_ai_streams:
            print(f"STATE WARNING: Stream ID {stream_id} already registered. Overwriting stop event.")
        stop_event = asyncio.Event()
        active_ai_streams[stream_id] = stop_event
        return stop_event

async def unregister_ai_stream(stream_id: str):
    async with active_ai_streams_lock:
        if stream_id in active_ai_streams:
            del active_ai_streams[stream_id]

async def signal_stop_ai_stream(stream_id: str) -> bool:
    async with active_ai_streams_lock:
        stop_event = active_ai_streams.get(stream_id)
        if stop_event:
            stop_event.set()
            return True
        return False

# --- WebSocket Connection Manager ---
class Connection:
    def __init__(self, websocket: WebSocket, queue: asyncio.Queue):
        self.websocket = websocket
        self.queue = queue

active_connections: Dict[str, List[Connection]] = {}
active_connections_lock = asyncio.Lock()

async def connect(session_id: str, connection: Connection):
    async with active_connections_lock:
        if session_id not in active_connections:
            active_connections[session_id] = []
        active_connections[session_id].append(connection)
        print(f"STATE: WebSocket connected to session {session_id}. Total connections: {len(active_connections[session_id])}")

async def disconnect(session_id: str, connection: Connection):
    async with active_connections_lock:
        if session_id in active_connections:
            try:
                active_connections[session_id].remove(connection)
                if not active_connections[session_id]:
                    del active_connections[session_id]
                print(f"STATE: WebSocket disconnected from session {session_id}.")
            except ValueError:
                pass

async def broadcast(session_id: str, message: dict, exclude_websocket: Optional[WebSocket] = None):
    queues_to_send: List[asyncio.Queue] = []
    async with active_connections_lock:
        if session_id in active_connections:
            for conn in active_connections[session_id]:
                if conn.websocket != exclude_websocket:
                    queues_to_send.append(conn.queue)
    
    if queues_to_send:
        for queue in queues_to_send:
            await queue.put(message)

# --- WebSocket Lobby Connection Manager ---
lobby_connections: List[WebSocket] = []
lobby_connections_lock = asyncio.Lock()

async def connect_to_lobby(websocket: WebSocket):
    await websocket.accept()
    async with lobby_connections_lock:
        lobby_connections.append(websocket)
        print(f"STATE: WebSocket connected to lobby. Total connections: {len(lobby_connections)}")

async def disconnect_from_lobby(websocket: WebSocket):
    async with lobby_connections_lock:
        try:
            lobby_connections.remove(websocket)
            print(f"STATE: WebSocket disconnected from lobby.")
        except ValueError:
            pass

async def broadcast_to_lobby(message: dict):
    websockets_to_send = [ws for ws in lobby_connections]
    
    if websockets_to_send:
        await asyncio.gather(
            *[ws.send_json(message) for ws in websockets_to_send],
            return_exceptions=True
        )
