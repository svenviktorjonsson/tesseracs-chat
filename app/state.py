# app/state.py

import asyncio
import json
import sqlite3
import datetime
from langchain.memory import ConversationBufferMemory # Ensure this import is correct for your LangChain version
from langchain_core.messages import messages_from_dict, messages_to_dict
from typing import Dict, Any, Optional
from . import database
import traceback # For detailed error logging

# In-memory cache for client-specific conversation memory
client_memory: Dict[str, ConversationBufferMemory] = {}

# For managing Docker code execution containers (if still used, otherwise can be removed if Docker utils are separate)
running_containers: Dict[str, Dict[str, Any]] = {} 
running_containers_lock = asyncio.Lock()

# For managing active AI streaming tasks to allow stopping them
active_ai_streams: Dict[str, asyncio.Event] = {}
active_ai_streams_lock = asyncio.Lock() # Lock for safe concurrent access to active_ai_streams

import re

def get_memory_for_client(session_id: str) -> ConversationBufferMemory:
    """
    Retrieves or creates ConversationBufferMemory for a specific session_id.
    Loads from DB, and for the LLM's context, appends any user-edited code blocks 
    after the original block in the history.
    """
    global client_memory
    if session_id in client_memory:
        return client_memory[session_id]

    conn = None
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # 1. Fetch all messages for the session
        cursor.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        )
        messages_rows = cursor.fetchall()

        # 2. Fetch all edited code blocks for the session
        edited_blocks = database.get_edited_code_blocks(session_id)

        # 3. Reconstruct message history, applying edits for context
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

                    # Check if an edit exists for this block
                    edited_code = edited_blocks.get(block_id)

                    if edited_code is not None:
                        # Append the edited version for the LLM's context
                        language = match.group(1)
                        edited_block_text = f"```{language}\n{edited_code}\n```"
                        return f"{original_block_text}\n\n(after edit by user:...)\n\n{edited_block_text}"
                    else:
                        # No edit, return the original block
                        return original_block_text

                # Use regex to find and amend code blocks in the content
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

def remove_memory_for_client(session_id: str):
    """Removes memory for a specific session_id from the in-memory cache."""
    global client_memory
    if session_id in client_memory:
        del client_memory[session_id]
        print(f"STATE: Memory cache cleared for session {session_id} due to data change.")

def save_memory_state_to_db(session_id: str, memory: Optional[ConversationBufferMemory]):
    """
    Saves the current state of the ConversationBufferMemory to the database for a given session_id.
    Includes detailed logging for debugging.
    """
    if not memory:
        print(f"STATE WARNING (save_memory): Attempted to save null memory for session {session_id}. Skipping.")
        return

    conn = None
    print(f"STATE ATTEMPT (save_memory): Saving memory state to DB for session {session_id}.")
    try:
        # Get messages from LangChain memory object
        messages = memory.chat_memory.messages
        # Convert messages to a list of dictionaries suitable for JSON serialization
        memory_state_list = messages_to_dict(messages) 
        memory_state_json = json.dumps(memory_state_list) # Serialize the list to a JSON string
        
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
    """Removes memory for a specific session_id from the in-memory cache."""
    global client_memory
    if session_id in client_memory:
        del client_memory[session_id]
        print(f"STATE: Memory removed from cache for session {session_id}")

# --- Functions to manage AI stream stop events ---
async def register_ai_stream(stream_id: str) -> asyncio.Event:
    """
    Registers a new AI stream and returns an event to signal its stopping.
    """
    async with active_ai_streams_lock:
        if stream_id in active_ai_streams:
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

async def signal_stop_ai_stream(stream_id: str) -> bool:
    """
    Sets the stop event for a given AI stream ID, if it exists.
    Returns True if signaled, False otherwise.
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
