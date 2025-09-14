import asyncio
import json
import sqlite3
import datetime
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import messages_from_dict, messages_to_dict
from typing import Dict, Any, Optional, List
from fastapi import WebSocket
from . import database
import traceback

client_memory: Dict[str, ConversationBufferMemory] = {}
running_containers: Dict[str, Dict[str, Any]] = {}
running_containers_lock = asyncio.Lock()
active_ai_streams: Dict[str, asyncio.Event] = {}
active_ai_streams_lock = asyncio.Lock()

import re

def get_memory_for_client(session_id: str) -> ConversationBufferMemory:
    global client_memory
    if session_id in client_memory:
        return client_memory[session_id]

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        )
        messages_rows = cursor.fetchall()

        edited_blocks = database.get_edited_code_blocks(session_id)

        reconstructed_messages = []
        for row in messages_rows:
            msg = dict(row)
            if msg['sender_type'] == 'ai' and msg['content'] and edited_blocks:
                code_block_counter = 0

                def process_code_block_for_context(match):
                    nonlocal code_block_counter
                    code_block_counter += 1

                    turn_id = msg.get('turn_id')
                    if turn_id is None:
                        return match.group(0)

                    block_id = f"code-block-turn{turn_id}-{code_block_counter}"
                    original_block_text = match.group(0)
                    edited_code = edited_blocks.get(block_id)

                    if edited_code is not None:
                        language = match.group(1)
                        edited_block_text = f"```{language}\n{edited_code}\n```"
                        return f"{original_block_text}\n\n(after edit by user:...)\n\n{edited_block_text}"
                    else:
                        return original_block_text

                code_block_regex = r"```(\w*)\n([\s\S]*?)\n```"
                msg['content'] = re.sub(code_block_regex, process_code_block_for_context, msg['content'])

            reconstructed_messages.append(msg)

        if reconstructed_messages:
            memory_instance = ConversationBufferMemory(return_messages=True, memory_key="history")

            langchain_messages = []
            for msg in reconstructed_messages:
                if msg['sender_type'] == 'user':
                    langchain_messages.append({"type": "human", "data": {"content": msg['content']}})
                elif msg['sender_type'] == 'ai':
                    langchain_messages.append({"type": "ai", "data": {"content": msg['content']}})

            memory_instance.chat_memory.messages = messages_from_dict(langchain_messages)
            client_memory[session_id] = memory_instance
            print(f"STATE: Memory loaded from DB and reconstructed for session {session_id}")
            return memory_instance

    except Exception as e:
        print(f"STATE ERROR: Failed to load/reconstruct memory for session {session_id}: {e}")
        traceback.print_exc()
    finally:
        if conn:
            conn.close()

    print(f"STATE: Creating new memory for session {session_id}")
    new_memory = ConversationBufferMemory(return_messages=True, memory_key="history")
    client_memory[session_id] = new_memory
    return new_memory

def save_memory_state_to_db(session_id: str, memory: Optional[ConversationBufferMemory]):
    if not memory:
        print(f"STATE WARNING (save_memory): Attempted to save null memory for session {session_id}. Skipping.")
        return

    conn = None
    print(f"STATE ATTEMPT (save_memory): Saving memory state to DB for session {session_id}.")
    try:
        messages = memory.chat_memory.messages
        memory_state_list = messages_to_dict(messages)
        memory_state_json = json.dumps(memory_state_list)
        
        current_time_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        conn = database.get_db_connection()
        cursor = conn.cursor()
        print(f"STATE PRE-EXECUTE (save_memory): About to execute INSERT OR REPLACE for session {session_id}.")
        cursor.execute(
            """
            INSERT OR REPLACE INTO session_memory_state 
            (session_id, memory_state_json, updated_at) 
            VALUES (?, ?, ?)
            """,
            (session_id, memory_state_json, current_time_utc)
        )
        print(f"STATE POST-EXECUTE (save_memory): SQL executed for session {session_id}.")
        conn.commit()
        print(f"STATE SUCCESS (save_memory): Memory saved and committed to DB for session {session_id}.")
    except json.JSONDecodeError as json_err:
        print(f"STATE JSON ERROR (save_memory): Failed to serialize memory for session {session_id}: {json_err}")
        traceback.print_exc()
        if conn:
            conn.rollback()
    except sqlite3.Error as db_err:
        print(f"STATE DB ERROR (save_memory): Failed to save memory state to DB for session {session_id}: {db_err}")
        traceback.print_exc()
        if conn:
            conn.rollback()
    except Exception as e:
        print(f"STATE UNEXPECTED ERROR (save_memory): Failed to save memory state for session {session_id}: {e}")
        traceback.print_exc()
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print(f"STATE FINALLY (save_memory): DB connection closed for session {session_id}.")

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
        print(f"STATE: AI stream {stream_id} registered for stopping.")
        return stop_event

async def unregister_ai_stream(stream_id: str):
    async with active_ai_streams_lock:
        if stream_id in active_ai_streams:
            del active_ai_streams[stream_id]
            print(f"STATE: AI stream {stream_id} unregistered.")
        else:
            print(f"STATE WARNING: Attempted to unregister non-existent AI stream {stream_id}.")

async def signal_stop_ai_stream(stream_id: str) -> bool:
    async with active_ai_streams_lock:
        stop_event = active_ai_streams.get(stream_id)
        if stop_event:
            stop_event.set()
            print(f"STATE: Stop signal sent to AI stream {stream_id}.")
            return True
        else:
            print(f"STATE WARNING: Attempted to signal stop for non-existent AI stream {stream_id}.")
            return False

# --- WebSocket Connection Manager ---
active_connections: Dict[str, List[WebSocket]] = {}
active_connections_lock = asyncio.Lock()

async def connect(session_id: str, websocket: WebSocket):
    async with active_connections_lock:
        if session_id not in active_connections:
            active_connections[session_id] = []
        active_connections[session_id].append(websocket)
        print(f"STATE: WebSocket connected to session {session_id}. Total connections: {len(active_connections[session_id])}")

async def disconnect(session_id: str, websocket: WebSocket):
    async with active_connections_lock:
        if session_id in active_connections:
            try:
                active_connections[session_id].remove(websocket)
                if not active_connections[session_id]:
                    del active_connections[session_id]
                print(f"STATE: WebSocket disconnected from session {session_id}.")
            except ValueError:
                print(f"STATE WARNING: WebSocket to disconnect not found in session {session_id}.")

async def broadcast(session_id: str, message: dict, exclude_websocket: Optional[WebSocket] = None):
    websockets_to_send = []
    async with active_connections_lock:
        if session_id in active_connections:
            for ws in active_connections[session_id]:
                if ws != exclude_websocket:
                    websockets_to_send.append(ws)
    
    if websockets_to_send:
        await asyncio.gather(
            *[ws.send_json(message) for ws in websockets_to_send],
            return_exceptions=True
        )


# In app/state.py, at the end of the file

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
            pass # Socket already removed

async def broadcast_to_lobby(message: dict):
    websockets_to_send = []
    async with lobby_connections_lock:
        websockets_to_send = [ws for ws in lobby_connections]
    
    print(f"--- LOG: 4. `broadcast_to_lobby` called. Broadcasting to {len(websockets_to_send)} clients. ---")
    print(f"--- LOG:    Message payload: {message}")

    if websockets_to_send:
        results = await asyncio.gather(
            *[ws.send_json(message) for ws in websockets_to_send],
            return_exceptions=True
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"--- LOG ERROR: Failed to send broadcast to client {i}: {result}")
    else:
        print("--- LOG: No clients in lobby to broadcast to.")