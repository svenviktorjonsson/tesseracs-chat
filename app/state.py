# app/state.py

import asyncio
import json
import sqlite3
import datetime
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import messages_from_dict, messages_to_dict
from typing import Dict, Any
from . import database

client_memory: Dict[str, ConversationBufferMemory] = {}
running_containers: Dict[str, Dict[str, Any]] = {}
running_containers_lock = asyncio.Lock()

def get_memory_for_client(session_id: str) -> ConversationBufferMemory:
    global client_memory
    if session_id in client_memory:
        return client_memory[session_id]

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
                memory_data = json.loads(row["memory_state_json"])
                if isinstance(memory_data, list):
                    memory_instance = ConversationBufferMemory(
                        return_messages=True,
                        memory_key="history"
                    )
                    loaded_messages = messages_from_dict(memory_data)
                    memory_instance.chat_memory.messages = loaded_messages
                    client_memory[session_id] = memory_instance
                    return memory_instance
            except (json.JSONDecodeError, Exception):
                 pass
    except sqlite3.Error:
         pass
    finally:
        if conn:
            conn.close()

    new_memory = ConversationBufferMemory(return_messages=True, memory_key="history")
    client_memory[session_id] = new_memory
    return new_memory

def save_memory_state_to_db(session_id: str, memory: ConversationBufferMemory):
    if not memory:
        return

    conn = None
    try:
        messages = memory.chat_memory.messages
        memory_state_list = messages_to_dict(messages)
        memory_state_json = json.dumps(memory_state_list)
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
    except (json.JSONDecodeError, sqlite3.Error, Exception):
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def remove_memory_for_client(session_id: str):
    global client_memory
    if session_id in client_memory:
        del client_memory[session_id]