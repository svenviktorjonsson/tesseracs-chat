# app/state.py

import asyncio
import json
import sqlite3
import datetime
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import messages_from_dict, messages_to_dict
from typing import Dict, Any, Optional # Added Optional
from . import database

# In-memory storage for client-specific conversation memory
client_memory: Dict[str, ConversationBufferMemory] = {}

# --- MODIFIED: Global state for WebSocket Connections and Running Containers (from your original temp.py) ---
# This was for Docker code execution, ensure it's still relevant or adapt as needed.
# For AI stream stopping, we'll add a new structure.
running_containers: Dict[str, Dict[str, Any]] = {} # For Docker code execution
running_containers_lock = asyncio.Lock() # Lock for running_containers

# --- ADDED: For managing active AI streaming tasks ---
# Stores asyncio.Event objects for active AI streams, keyed by a unique stream ID (e.g., f"{client_js_id}_{turn_id}")
# Each event, when set, signals the corresponding AI stream to stop.
active_ai_streams: Dict[str, asyncio.Event] = {}
active_ai_streams_lock = asyncio.Lock() # Lock for safe concurrent access to active_ai_streams
# --- END OF ADDED ---


def get_memory_for_client(session_id: str) -> ConversationBufferMemory:
    """
    Retrieves or creates ConversationBufferMemory for a specific session_id.
    Loads from DB if available, otherwise creates a new one.
    """
    global client_memory
    if session_id in client_memory:
        return client_memory[session_id]

    # Attempt to load from database
    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT memory_state_json FROM session_memory_state WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()

        if row and row["memory_state_json"]:
            try:
                memory_data_list = json.loads(row["memory_state_json"]) # Expecting a list of message dicts
                if isinstance(memory_data_list, list):
                    # Create a new memory instance
                    memory_instance = ConversationBufferMemory(
                        return_messages=True, 
                        memory_key="history"
                    )
                    # Load messages into the new instance
                    loaded_messages = messages_from_dict(memory_data_list)
                    memory_instance.chat_memory.messages = loaded_messages
                    
                    client_memory[session_id] = memory_instance
                    print(f"STATE: Memory loaded from DB for session {session_id}")
                    return memory_instance
                else:
                    print(f"STATE WARNING: Memory data for session {session_id} is not a list. Creating new memory.")
            except (json.JSONDecodeError, TypeError, Exception) as e:
                # Catch TypeError if messages_from_dict fails, or other errors
                print(f"STATE ERROR: Failed to load/parse memory for session {session_id} from DB: {e}. Creating new memory.")
                # Fall through to create new memory
    except sqlite3.Error as db_err:
        print(f"STATE DB ERROR: Could not query session_memory_state for session {session_id}: {db_err}")
        # Fall through to create new memory
    finally:
        if conn:
            conn.close()

    # If not found in cache or DB, or if loading failed, create new memory
    print(f"STATE: Creating new memory for session {session_id}")
    new_memory = ConversationBufferMemory(return_messages=True, memory_key="history")
    client_memory[session_id] = new_memory
    return new_memory

def save_memory_state_to_db(session_id: str, memory: Optional[ConversationBufferMemory]):
    """
    Saves the current state of the ConversationBufferMemory to the database for a given session_id.
    """
    if not memory:
        print(f"STATE WARNING: Attempted to save null memory for session {session_id}. Skipping.")
        return

    conn = None
    try:
        # Get messages from memory and convert to a list of dictionaries
        messages = memory.chat_memory.messages
        memory_state_list = messages_to_dict(messages) # Should return List[Dict[str, Any]]
        memory_state_json = json.dumps(memory_state_list) # Serialize the list of dicts
        
        current_time_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO session_memory_state 
            (session_id, memory_state_json, updated_at) 
            VALUES (?, ?, ?)
            """,
            (session_id, memory_state_json, current_time_utc)
        )
        conn.commit()
        # print(f"STATE: Memory saved to DB for session {session_id}") # Can be verbose
    except (json.JSONDecodeError, sqlite3.Error, Exception) as e:
        # Catch potential errors during serialization or DB operation
        print(f"STATE ERROR: Failed to save memory state to DB for session {session_id}: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def remove_memory_for_client(session_id: str):
    """Removes memory for a specific session_id from the in-memory cache."""
    global client_memory
    if session_id in client_memory:
        del client_memory[session_id]
        print(f"STATE: Memory removed from cache for session {session_id}")

# --- ADDED: Functions to manage AI stream stop events ---
async def register_ai_stream(stream_id: str) -> asyncio.Event:
    """
    Registers a new AI stream and returns an event to signal its stopping.
    """
    async with active_ai_streams_lock:
        if stream_id in active_ai_streams:
            # This case should ideally be handled (e.g., stop previous before starting new)
            # or ensure stream_ids are unique enough (e.g., include a UUID).
            print(f"STATE WARNING: Stream ID {stream_id} already registered. Overwriting stop event.")
        stop_event = asyncio.Event()
        active_ai_streams[stream_id] = stop_event
        print(f"STATE: AI stream {stream_id} registered for stopping.")
        return stop_event

async def unregister_ai_stream(stream_id: str):
    """
    Unregisters an AI stream, typically when it finishes or is stopped.
    """
    async with active_ai_streams_lock:
        if stream_id in active_ai_streams:
            del active_ai_streams[stream_id]
            print(f"STATE: AI stream {stream_id} unregistered.")
        else:
            print(f"STATE WARNING: Attempted to unregister non-existent AI stream {stream_id}.")

async def signal_stop_ai_stream(stream_id: str):
    """
    Sets the stop event for a given AI stream ID, if it exists.
    """
    async with active_ai_streams_lock:
        stop_event = active_ai_streams.get(stream_id)
        if stop_event:
            stop_event.set()
            print(f"STATE: Stop signal sent to AI stream {stream_id}.")
            return True
        else:
            print(f"STATE WARNING: Attempted to signal stop for non-existent AI stream {stream_id}.")
            return False
# --- END OF ADDED ---
