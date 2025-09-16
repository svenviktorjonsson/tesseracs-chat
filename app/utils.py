# app/utils.py

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
import html
import traceback
from typing import Any

def escape_html(s: str) -> str:
    """
    Escapes a string for safe inclusion in HTML, preventing XSS.
    """
    if not isinstance(s, str):
        s = str(s) # Ensure it's a string
    return html.escape(s)

def is_valid_email(email: str) -> bool:
    """
    Validates an email address. A simple check.
    """
    if not email:
        return False
    # Basic regex for email validation
    import re
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if re.match(pattern, email):
        return True
    return False

async def send_ws_message(websocket: WebSocket, message_type: str, payload: Any):
    """Safely sends a JSON message over the WebSocket with improved logging."""
    if websocket.client_state != WebSocketState.CONNECTED:
        print(f"[utils] ✗ WebSocket not connected (state: {websocket.client_state.name}), cannot send message")
        return False
        
    try:
        message = {"type": message_type, "payload": payload}
        await websocket.send_json(message)
        return True
    except WebSocketDisconnect:
        print(f"[utils] ✗ WebSocket disconnected while trying to send {message_type}")
        return False
    except Exception as e:
        print(f"[utils] ✗ Error sending WebSocket message ({message_type}): {e}")
        traceback.print_exc()
        return False