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
from fastapi.responses import HTMLResponse, FileResponse # Added FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

# Assuming these imports point to files relative to this script's location
# Adjust if your project structure is different
from . import config
from . import state
from . import llm
from . import docker_utils
from . import utils # For send_ws_message

# --- FastAPI App Initialization ---
app = FastAPI(title="Tesseracs Chat")

# --- Static Files Setup ---

# Ensure the base static directory exists (e.g., app/static)
if not config.STATIC_DIR or not config.STATIC_DIR.is_dir():
    print(f"CRITICAL ERROR: Base static directory path is invalid or not found: {config.STATIC_DIR}. Exiting.")
    sys.exit(1)
else:
    print(f"Base static directory found: {config.STATIC_DIR}")

# Define the path to the 'dist' directory within the static directory
dist_dir = Path(config.STATIC_DIR) / "dist"

# Ensure the 'dist' directory exists
if not dist_dir.is_dir():
    print(f"CRITICAL ERROR: Bundled assets directory not found: {dist_dir}. Did you run 'npm run build'? Exiting.")
    sys.exit(1)
else:
    print(f"Bundled assets directory found: {dist_dir}")

# Mount the 'dist' directory to serve requests starting with '/dist'
# This allows index.html (served from '/') to find ./dist/input.css etc.
app.mount("/dist", StaticFiles(directory=dist_dir), name="dist_assets")
print(f"Mounted bundled assets directory '{dist_dir}' at '/dist'")

# Optional: Mount the base static directory if you have other assets there
# If you only have index.html and the dist folder, you might not need this.
# If you keep it, ensure it doesn't conflict with other routes.
# app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
# print(f"Mounted base static directory '{config.STATIC_DIR}' at '/static'")


# --- FastAPI HTTP Routes ---
@app.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    """Serves the main index.html file."""
    # Serve index.html from the base static directory
    html_file_path = Path(config.STATIC_DIR) / "index.html"
    if not html_file_path.is_file():
        print(f"ERROR: index.html not found at expected location: {html_file_path}")
        raise HTTPException(status_code=404, detail="index.html not found")
    try:
        # Use FileResponse for potentially better handling of static HTML
        return FileResponse(html_file_path)
        # # Alternative: Read and return content
        # with open(html_file_path, "r", encoding="utf-8") as f:
        #     html_content = f.read()
        # return HTMLResponse(content=html_content)
    except Exception as e:
        print(f"ERROR serving index.html: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error serving index.html: {e}")

# --- WebSocket Endpoint ---
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """Handles WebSocket connections for chat and code execution."""
    await websocket.accept()
    print(f"WebSocket connection accepted for client: {client_id}")
    memory = state.get_memory_for_client(client_id)
    # Ensure model instance is handled correctly (singleton or per-request)
    # model_instance = llm.get_model() # Assuming get_model handles initialization

    def load_memory_for_current_client(_):
        """Loads memory variables specific to the current client."""
        loaded_vars = memory.load_memory_variables({})
        return loaded_vars.get("history", [])

    try:
        # Create chain within the connection scope if it depends on client memory
        # Use the model instance already initialized in llm.py
        chain = llm.create_chain(load_memory_for_current_client)
        print(f"LCEL Chain created for client: {client_id}")
    except Exception as chain_init_error:
         print(f"ERROR creating LCEL chain for {client_id}: {chain_init_error}")
         traceback.print_exc()
         await websocket.close(code=1011) # Internal Server Error
         return # Exit websocket handler

    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WebSocket no longer connected for {client_id} before receive. Breaking loop.")
                break

            received_data = await websocket.receive_text()

            try:
                # Attempt to parse as JSON for potential commands
                message_data = json.loads(received_data)
                message_type = message_data.get("type")
                payload = message_data.get("payload")

                # Check if it looks like a valid command structure
                if message_type and payload and isinstance(payload, dict):
                    code_block_id = payload.get("code_block_id")
                    if not code_block_id:
                        print(f"Received JSON command without code_block_id: {message_data}")
                        continue # Ignore invalid command

                    # Handle 'run_code' command
                    if message_type == "run_code":
                        language = payload.get("language")
                        code = payload.get("code")
                        if language and code is not None:
                            print(f"Received 'run_code' request for block {code_block_id} ({language}) from {client_id}")
                            # Run code execution in background task
                            asyncio.create_task(
                                docker_utils.run_code_in_docker_stream(websocket, client_id, code_block_id, language, code)
                            )
                        else:
                            print(f"Invalid 'run_code' payload received: {payload}")
                            await utils.send_ws_message(websocket, "code_finished", {
                                "code_block_id": code_block_id, "exit_code": -1,
                                "error": "Invalid run_code request payload from client."
                            })

                    # Handle 'stop_code' command
                    elif message_type == "stop_code":
                        print(f"Received 'stop_code' request for block {code_block_id} from {client_id}")
                        # Stop container in background task
                        asyncio.create_task(
                            docker_utils.stop_docker_container(code_block_id)
                        )

                    # Handle unknown command types
                    else:
                        print(f"Received unknown JSON command type '{message_type}' from {client_id}")

                # If not a valid command structure, treat as chat
                else:
                     print(f"Received invalid JSON structure from {client_id}, treating as chat: {received_data[:100]}...")
                     await handle_chat_message(chain, memory, websocket, client_id, received_data)

            # If it's not JSON, treat as a chat message
            except json.JSONDecodeError:
                await handle_chat_message(chain, memory, websocket, client_id, received_data)
            # Catch potential errors during command/chat handling within the loop
            except Exception as handler_exc:
                 print(f"ERROR handling message for {client_id}: {handler_exc}")
                 traceback.print_exc()
                 # Optionally send an error message back to the client
                 try:
                      await utils.send_ws_message(websocket, "error", {"message": f"Server error processing message: {handler_exc}"})
                 except Exception:
                      pass # Ignore if sending error fails

    except WebSocketDisconnect:
        print(f"WebSocket disconnected during receive/process for client: {client_id}")
    except Exception as e:
        print(f"Unknown Error in WebSocket loop for client {client_id}: {e}")
        traceback.print_exc()
    finally:
        print(f"Cleaning up resources for client: {client_id}")
        state.remove_memory_for_client(client_id)
        # Ensure cleanup happens even if connection drops unexpectedly
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
    """Handles incoming chat messages, streams response, and saves context."""
    # Add a debug print here to see the config value when the chain is used
    print(f"DEBUG handle_chat_message: Using config.MODEL_ID = {config.MODEL_ID}")
    print(f"Handling chat message from {client_id}: '{user_input[:50]}...'")
    full_response = ""
    try:
        # Stream the response from the language model chain
        async for chunk in chain.astream({"input": user_input}):
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"WebSocket disconnected during chat stream for {client_id}. Aborting send.")
                return # Stop streaming if client disconnects
            await websocket.send_text(chunk)
            full_response += chunk # Accumulate the full response for memory
    except Exception as chain_exc:
        # Handle errors during the language model processing
        error_msg = f"<ERROR>Error processing message: {chain_exc}"
        print(f"ERROR during chain execution for {client_id}: {chain_exc}")
        traceback.print_exc()
        # Try to send an error message back to the client
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(error_msg)
            except Exception as send_err:
                 print(f"Error sending chain exception message to {client_id}: {send_err}")
        return # Exit without saving context if the chain failed

    # If streaming finished and client is still connected, send End Of Stream marker
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.send_text("<EOS>")
            print(f"Finished streaming chat response to {client_id}")
            # Save the interaction to memory
            memory.save_context({"input": user_input}, {"output": full_response})
            print(f"Saved chat context to memory for client: {client_id}")
        except Exception as send_eos_err:
             print(f"Error sending <EOS> or saving context for {client_id}: {send_eos_err}")
    else:
         print(f"WebSocket disconnected before sending <EOS> for {client_id}.")


# --- Function to run the server ---
def start_server():
    """Initializes and runs the Uvicorn server."""
    host = "127.0.0.1"
    port = 8001 # Ensure this matches the port you access in the browser
    url = f"http://{host}:{port}"

    print("-" * 30)
    print(f"Starting server at {url}...")
    print(f"Using Ollama base URL: {config.OLLAMA_BASE_URL}")
    # --- ADDED DEBUG PRINT ---
    print(f"DEBUG start_server: Configured Model ID = {config.MODEL_ID}")
    # -------------------------
    print(f"Using Model ID: {config.MODEL_ID}") # Keep original print too
    print(f"Base static directory: {config.STATIC_DIR}")
    print(f"Bundled assets directory: {dist_dir}") # Print dist_dir path
    print(f"Supported execution languages: {list(config.SUPPORTED_LANGUAGES.keys())}")

    # Check Docker availability
    if docker_utils.get_docker_client() is None:
        print("WARNING: Docker client unavailable. Code execution will fail.")
    else:
        print("Docker client available.")
    print("-" * 30)

    # Attempt to open the browser automatically
    print(f"Attempting to open browser at {url}...")
    try:
        webbrowser.open(url)
    except Exception as browser_err:
        print(f"Warning: Could not automatically open browser: {browser_err}")
        print(f"Please navigate to {url} manually.")

    # Run the Uvicorn server
    # Note: reload=True is useful for development but should be False in production
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=True # Set to False for production
        # reload_dirs=[str(Path(__file__).parent)] # Optional: Specify dirs to watch for reload
    )

# --- Main Execution Block ---
# Allows running the server script directly
if __name__ == "__main__":
    start_server()
