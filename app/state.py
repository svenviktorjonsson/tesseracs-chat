import asyncio
from langchain.memory import ConversationBufferMemory
from typing import Dict, Any
from docker.models.containers import Container # For type hinting
from fastapi import WebSocket

# --- Global State for WebSocket Connections and Running Containers ---
client_memory: Dict[str, ConversationBufferMemory] = {}

# Stores info about currently running code executions
# Format: { "code_block_id": {"container": Container, "stream_task": asyncio.Task, "client_id": str, "websocket": WebSocket, "stop_event": asyncio.Event} }
running_containers: Dict[str, Dict[str, Any]] = {}
running_containers_lock = asyncio.Lock() # Lock for safe concurrent access

# --- Memory Management Functions ---
def get_memory_for_client(client_id: str) -> ConversationBufferMemory:
    """Retrieves or creates memory for a specific client."""
    global client_memory
    if client_id not in client_memory:
        client_memory[client_id] = ConversationBufferMemory(return_messages=True, memory_key="history")
        print(f"Initialized new memory for client: {client_id}")
    return client_memory[client_id]

def remove_memory_for_client(client_id: str):
    """Removes memory when a client disconnects."""
    global client_memory
    if client_id in client_memory:
        del client_memory[client_id]
        print(f"Removed memory for client: {client_id}")