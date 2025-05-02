# app/main.py
import os
import sys
import uvicorn
import webbrowser
import traceback
from pathlib import Path
import asyncio
import json
from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    WebSocket,
    WebSocketDisconnect
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

from . import config
from . import state
from . import llm
from . import docker_utils
from . import utils # For send_ws_message

# --- FastAPI App Initialization ---
app = FastAPI(title="Ollama Web Chat")

# --- Static Files Setup ---
if not config.STATIC_DIR or not config.STATIC_DIR.is_dir():
     print(f"CRITICAL ERROR: Static directory path is invalid or not found: {config.STATIC_DIR}. Exiting.")
     sys.exit(1)

app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
print(f"Mounted static directory '{config.STATIC_DIR}' at '/static'")

# --- FastAPI HTTP Routes ---
@app.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    html_file_path = config.STATIC_DIR / "index.html"
    if not html_file_path.is_file():
        print(f"ERROR: index.html not found at expected location: {html_file_path}")
        raise HTTPException(status_code=404, detail="index.html not found")
    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except Exception as e:
        print(f"ERROR reading index.html: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error reading index.html: {e}")

# --- WebSocket Endpoint ---
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    print(f"WebSocket connection accepted for client: {client_id}")
    memory = state.get_memory_for_client(client_id)
    model_instance = llm.get_model()

    def load_memory_for_current_client(_):
        loaded_vars = memory.load_memory_variables({})
        return loaded_vars.get("history", [])

    chain = llm.create_chain(load_memory_for_current_client)
    print(f"LCEL Chain created for client: {client_id}")

    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WebSocket no longer connected for {client_id} before receive. Breaking loop.")
                break

            received_data = await websocket.receive_text()

            try:
                message_data = json.loads(received_data)
                message_type = message_data.get("type")
                payload = message_data.get("payload")

                if message_type and payload and isinstance(payload, dict):
                    code_block_id = payload.get("code_block_id")
                    if not code_block_id:
                        print(f"Received JSON command without code_block_id: {message_data}")
                        continue

                    if message_type == "run_code":
                        language = payload.get("language")
                        code = payload.get("code")
                        if language and code is not None:
                            print(f"Received 'run_code' request for block {code_block_id} ({language}) from {client_id}")
                            asyncio.create_task(
                                docker_utils.run_code_in_docker_stream(websocket, client_id, code_block_id, language, code)
                            )
                        else:
                            print(f"Invalid 'run_code' payload received: {payload}")
                            await utils.send_ws_message(websocket, "code_finished", {
                                "code_block_id": code_block_id, "exit_code": -1,
                                "error": "Invalid run_code request payload from client."
                            })

                    elif message_type == "stop_code":
                        print(f"Received 'stop_code' request for block {code_block_id} from {client_id}")
                        asyncio.create_task(
                            docker_utils.stop_docker_container(code_block_id)
                        )

                    else:
                        print(f"Received unknown JSON command type '{message_type}' from {client_id}")

                else:
                     print(f"Received invalid JSON structure from {client_id}, treating as chat: {received_data[:100]}...")
                     await handle_chat_message(chain, memory, websocket, client_id, received_data)

            except json.JSONDecodeError:
                await handle_chat_message(chain, memory, websocket, client_id, received_data)

    except WebSocketDisconnect:
        print(f"WebSocket disconnected during receive/process for client: {client_id}")
    except Exception as e:
        print(f"Unknown Error in WebSocket loop for client {client_id}: {e}")
        traceback.print_exc()
    finally:
        print(f"Cleaning up resources for client: {client_id}")
        state.remove_memory_for_client(client_id)
        await docker_utils.cleanup_client_containers(client_id)
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=1000)
                print(f"WebSocket closed gracefully for {client_id}")
            except Exception as close_exc:
                print(f"Ignoring error during explicit WebSocket close for {client_id}: {close_exc}")
        else:
             print(f"WebSocket already closed for {client_id} during cleanup.")


async def handle_chat_message(chain, memory, websocket: WebSocket, client_id: str, user_input: str):
    print(f"Handling chat message from {client_id}: '{user_input[:50]}...'")
    full_response = ""
    try:
        async for chunk in chain.astream({"input": user_input}):
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WebSocket disconnected during chat stream for {client_id}. Aborting send.")
                return
            await websocket.send_text(chunk)
            full_response += chunk
    except Exception as chain_exc:
        error_msg = f"<ERROR>Error processing message: {chain_exc}"
        print(f"ERROR during chain execution for {client_id}: {chain_exc}")
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(error_msg)
            except Exception as send_err:
                 print(f"Error sending chain exception message to {client_id}: {send_err}")
        return # Don't save context if chain failed

    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.send_text("<EOS>")
            print(f"Finished streaming chat response to {client_id}")
            memory.save_context({"input": user_input}, {"output": full_response})
            print(f"Saved chat context to memory for client: {client_id}")
        except Exception as send_eos_err:
             print(f"Error sending <EOS> or saving context for {client_id}: {send_eos_err}")
    else:
         print(f"WebSocket disconnected before sending <EOS> for {client_id}.")


# --- Function to run the server ---
def start_server():
    host = "127.0.0.1"
    port = 8001
    url = f"http://{host}:{port}"

    print("-" * 30)
    print(f"Starting server at {url}...")
    print(f"Using Ollama base URL: {config.OLLAMA_BASE_URL}")
    print(f"Using Model ID: {config.MODEL_ID}")
    print(f"Static files served from: {config.STATIC_DIR}")
    print(f"Supported execution languages: {list(config.SUPPORTED_LANGUAGES.keys())}")

    if docker_utils.get_docker_client() is None:
        print("WARNING: Docker client unavailable. Code execution will fail.")
    else:
        print("Docker client available.")
    print("-" * 30)

    print(f"Attempting to open browser at {url}...")
    try:
        webbrowser.open(url)
    except Exception as browser_err:
        print(f"Warning: Could not automatically open browser: {browser_err}")
        print(f"Please navigate to {url} manually.")

    uvicorn.run("app.main:app", host=host, port=port, log_level="info", reload=True)

# --- Main Execution Block ---
# This allows running via 'python -m app.main' or 'python app/main.py'
# It's also the entry point for the poetry script 'app.main:start_server'
if __name__ == "__main__":
    start_server()