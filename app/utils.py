# app/utils.py
import json
import traceback
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

async def send_ws_message(websocket: WebSocket, message_type: str, payload: dict):
    """Safely sends a JSON message over the WebSocket."""
    # Check state before sending (no change needed here)
    if websocket.client_state != WebSocketState.CONNECTED:
        print(f"[utils] WebSocket not connected, cannot send {message_type} for {payload.get('code_block_id', 'N/A')}")
        return
    try:
        await websocket.send_json({"type": message_type, "payload": payload})
    except WebSocketDisconnect:
        print(f"[utils] WebSocket disconnected while trying to send {message_type} for {payload.get('code_block_id', 'N/A')}")
    except Exception as e:
        # Catch other potential errors like Runtime Error if connection closed during send
        print(f"[utils] Error sending WebSocket message ({message_type}) for {payload.get('code_block_id', 'N/A')}: {e}")
        # traceback.print_exc() # Optional: uncomment for more detail