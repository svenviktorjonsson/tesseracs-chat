# app/utils.py

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
import re
import html
import traceback

def escape_html(s: str) -> str:
    """
    Escapes a string for safe inclusion in HTML, preventing XSS.
    """
    if not isinstance(s, str):
        s = str(s) # Ensure it's a string
    return html.escape(s)

def is_valid_email(email: str) -> bool:
    """
    Validates an email address.
    (This is a basic example, consider a more robust library for production)
    """
    if not email:
        return False
    # Basic regex for email validation
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if re.match(pattern, email):
        return True
    return False

async def send_ws_message(websocket: WebSocket, message_type: str, payload: dict):
    """Safely sends a JSON message over the WebSocket."""
    code_block_id = payload.get('code_block_id', 'N/A')
    
    print(f"[utils] Attempting to send {message_type} for {code_block_id}")
    
    # Check state before sending
    if websocket.client_state != WebSocketState.CONNECTED:
        print(f"[utils] ✗ WebSocket not connected (state: {websocket.client_state.name}), cannot send {message_type} for {code_block_id}")
        return False
        
    try:
        message = {"type": message_type, "payload": payload}
        print(f"[utils] Sending message: {message}")
        await websocket.send_json(message)
        print(f"[utils] ✓ Successfully sent {message_type} for {code_block_id}")
        return True
    except WebSocketDisconnect:
        print(f"[utils] ✗ WebSocket disconnected while trying to send {message_type} for {code_block_id}")
        return False
    except Exception as e:
        # Catch other potential errors like Runtime Error if connection closed during send
        print(f"[utils] ✗ Error sending WebSocket message ({message_type}) for {code_block_id}: {e}")
        traceback.print_exc() # Enable this to see full error details
        return False