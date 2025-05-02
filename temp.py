import os
import asyncio
import sys
import uvicorn
import webbrowser
import tempfile
import shutil
import json # Added for WebSocket message parsing/sending
import traceback # For detailed error logging
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse # JSONResponse no longer needed for run_code
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState # Import WebSocketState
from pydantic import BaseModel # Keep for potential future HTTP models
import docker # Docker SDK
from docker.errors import DockerException, ImageNotFound, APIError, NotFound # Docker specific errors
from docker.models.containers import Container # For type hinting
from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain.memory import ConversationBufferMemory
from langchain_core.runnables import RunnablePassthrough # RunnableLambda no longer needed here
from langchain_core.messages import HumanMessage, AIMessage

# --- Configuration ---
load_dotenv()
MODEL_ID = os.getenv("MODEL_ID", "qwen3")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# NO_THINK_PREFIX is handled by frontend, backend receives prefixed message
NO_THINK_PREFIX = "\\no_think"

# --- Docker Configuration ---
SUPPORTED_LANGUAGES = {
    "python": {
        "image": "python:3.11-slim",
        "filename": "script.py",
        "command": ["python", "-u", "/app/script.py"] # Added -u for unbuffered output
    },
    "javascript": {
        "image": "node:18-alpine",
        "filename": "script.js",
        "command": ["node", "/app/script.js"]
    },
    # Add more languages here if needed
}
DOCKER_TIMEOUT_SECONDS = 30 # Increased timeout for potentially longer runs
DOCKER_MEM_LIMIT = "128m"

# --- FastAPI App Initialization ---
# Ensure the app object is created correctly
app = FastAPI(title="Ollama Web Chat")

# --- Docker Client Initialization ---
docker_client = None
try:
    docker_client = docker.from_env()
    docker_client.ping()
    print("Successfully connected to Docker daemon.")
except DockerException as e:
    print(f"CRITICAL WARNING: Could not connect to Docker daemon: {e}")
    print("Code execution via Docker will be unavailable.")
    docker_client = None

# --- Static Files Setup ---
# Determine the static files directory relative to this script's location
# This assumes main.py is either in the project root or inside an 'app' directory.
script_location = Path(__file__).parent
static_dir_in_app = script_location / "static"
static_dir_at_root = script_location.parent / "static" # If main.py is in 'app' dir

if static_dir_in_app.is_dir():
    static_dir = static_dir_in_app
    print(f"Found static directory at: {static_dir}")
elif script_location.name == "app" and static_dir_at_root.is_dir():
     # If script is in 'app' and 'static' is sibling to 'app'
     static_dir = static_dir_at_root
     print(f"Found static directory at: {static_dir}")
else:
    # Fallback check if script is in root and static is in root
    if (script_location.parent / "static").is_dir():
         static_dir = script_location.parent / "static"
         print(f"Found static directory at: {static_dir}")
    else:
        print(f"CRITICAL ERROR: Static directory not found near '{script_location}'. Looked for '{static_dir_in_app}' and '{static_dir_at_root}'. Exiting.")
        sys.exit(1)

# Mount static files - THIS MUST BE CORRECT FOR CSS/JS
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
print(f"Mounted static directory '{static_dir}' at '/static'")


# --- LangChain Setup ---
try:
    model = OllamaLLM(model=MODEL_ID, base_url=OLLAMA_BASE_URL)
    print(f"Successfully initialized OllamaLLM: {MODEL_ID} at {OLLAMA_BASE_URL}")
except Exception as e:
    print(f"CRITICAL ERROR: OllamaLLM init failed: {e}"); sys.exit(1)

# --- Global State for WebSocket Connections and Running Containers ---
client_memory = {}
# Stores info about currently running code executions
# Format: { "code_block_id": {"container": Container, "stream_task": asyncio.Task, "client_id": str, "websocket": WebSocket, "stop_event": asyncio.Event} }
running_containers = {}
running_containers_lock = asyncio.Lock() # Lock for safe concurrent access

# --- Memory Management Functions (Unchanged) ---
def get_memory_for_client(client_id: str) -> ConversationBufferMemory:
    """Retrieves or creates memory for a specific client."""
    if client_id not in client_memory:
        client_memory[client_id] = ConversationBufferMemory(return_messages=True, memory_key="history")
        print(f"Initialized new memory for client: {client_id}")
    return client_memory[client_id]

def remove_memory_for_client(client_id: str):
    """Removes memory when a client disconnects."""
    if client_id in client_memory:
        del client_memory[client_id]
        print(f"Removed memory for client: {client_id}")

# --- LangChain Prompt and Chain Setup (Unchanged) ---
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful AI assistant chatting in a web interface. Answer the user's questions concisely. Always use katex for math ($...$ or $$...$$). For a literal dollar sign use \\$. When providing code, use standard markdown code blocks (e.g., ```python ... ```)."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}")
])
output_parser = StrOutputParser()

# --- Helper Functions for WebSocket Code Execution (Unchanged from previous version) ---

async def send_ws_message(websocket: WebSocket, message_type: str, payload: dict):
    """Safely sends a JSON message over the WebSocket."""
    # Check state before sending
    if websocket.client_state != WebSocketState.CONNECTED:
         print(f"WebSocket not connected, cannot send {message_type} for {payload.get('code_block_id', 'N/A')}")
         return
    try:
        await websocket.send_json({"type": message_type, "payload": payload})
    except WebSocketDisconnect:
        print(f"WebSocket disconnected while trying to send {message_type} for {payload.get('code_block_id', 'N/A')}")
    except Exception as e:
        # Catch other potential errors like Runtime Error if connection closed during send
        print(f"Error sending WebSocket message ({message_type}) for {payload.get('code_block_id', 'N/A')}: {e}")
        # traceback.print_exc() # Optional: uncomment for more detail

# This function runs in a separate thread via run_in_executor
def sync_log_streamer(container: Container, websocket: WebSocket, code_block_id: str, loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event):
    """
    Synchronously iterates through Docker logs and schedules sending messages
    back to the main asyncio loop. Runs in an executor thread.
    """
    try:
        # Note: follow=True keeps the stream open until container stops or stream is closed.
        log_stream = container.logs(stream=True, follow=True, stdout=True, stderr=True)
        print(f"[Thread-{code_block_id}] Starting log iteration for container {container.short_id}")

        for line_bytes in log_stream:
            if stop_event.is_set():
                print(f"[Thread-{code_block_id}] Stop event set, breaking log iteration.")
                break

            line_str = line_bytes.decode('utf-8', errors='replace')
            # Schedule the send_ws_message coroutine to run on the main event loop
            asyncio.run_coroutine_threadsafe(
                send_ws_message(websocket, "code_output", {
                    "code_block_id": code_block_id,
                    "stream": "stdout", # Sending all as stdout for simplicity, could try parsing later
                    "data": line_str
                }),
                loop # Pass the main event loop
            )

        print(f"[Thread-{code_block_id}] Finished log iteration for container {container.short_id}")

    except Exception as e:
        # Log error from the thread
        print(f"[Thread-{code_block_id}] Error during log streaming: {e}")
        # Don't print traceback here usually, as it might be expected on stop
        # traceback.print_exc()
        # Try to send an error message back to the client via the main loop
        asyncio.run_coroutine_threadsafe(
            send_ws_message(websocket, "code_output", {
                "code_block_id": code_block_id,
                "stream": "error",
                "data": f"\n[Error streaming logs from container: {str(e)}]"
            }),
            loop
        )

async def stream_docker_logs_via_executor(websocket: WebSocket, container: Container, code_block_id: str, stop_event: asyncio.Event):
    """
    Runs the synchronous log streamer function in an executor thread.
    """
    loop = asyncio.get_running_loop()
    print(f"Scheduling log streaming task in executor for {code_block_id}")
    # Run the sync function in the default executor
    await loop.run_in_executor(
        None, # Use default thread pool executor
        sync_log_streamer, # The function to run
        container, websocket, code_block_id, loop, stop_event # Arguments for the function
    )
    print(f"Executor task for log streaming finished for {code_block_id}")


async def run_code_in_docker_stream(websocket: WebSocket, client_id: str, code_block_id: str, language: str, code: str):
    """Runs code in Docker, streams output via WebSocket, and manages container lifecycle."""
    global docker_client, running_containers, running_containers_lock

    if not docker_client:
        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id, "exit_code": -1,
            "error": "Docker service is unavailable on the server."
        })
        return

    lang_key = language.lower()
    lang_config = SUPPORTED_LANGUAGES.get(lang_key)
    if not lang_config:
        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id, "exit_code": -1,
            "error": f"Language '{language}' not supported for execution."
        })
        return

    image_name = lang_config["image"]
    script_filename = lang_config["filename"]
    command = lang_config["command"]
    container_obj: Container | None = None
    stream_task: asyncio.Task | None = None
    tmpdir_obj = None # To hold the TemporaryDirectory object
    stop_event = asyncio.Event() # Event to signal cancellation to the streamer thread

    try:
        # Create temporary directory safely
        tmpdir_obj = tempfile.TemporaryDirectory()
        tmpdir = tmpdir_obj.name # Get the path string
        host_script_path = Path(tmpdir) / script_filename
        with open(host_script_path, "w", encoding="utf-8") as f:
            f.write(code)

        print(f"Attempting to run block {code_block_id} ({lang_key}) in Docker image {image_name}...")

        # Run the container detached
        container_obj = docker_client.containers.run(
            image=image_name,
            command=command,
            volumes={tmpdir: {'bind': '/app', 'mode': 'ro'}}, # Read-only mount
            working_dir='/app',
            mem_limit=DOCKER_MEM_LIMIT,
            stdout=True,
            stderr=True,
            detach=True, # Run in background
            # auto_remove=True, # Cannot auto-remove if we need to wait/get logs/stop
        )
        print(f"Container {container_obj.short_id} started for {code_block_id}")

        # Start the log streaming task using the executor helper
        stream_task = asyncio.create_task(
            stream_docker_logs_via_executor(websocket, container_obj, code_block_id, stop_event),
            name=f"log_stream_{code_block_id}" # Give the task a name for debugging
        )

        # Store container and task info BEFORE waiting
        async with running_containers_lock:
            if code_block_id in running_containers:
                 # This should ideally not happen if frontend disables button, but handle defensively
                 print(f"WARNING: Code block {code_block_id} was already running. Stopping previous run.")
                 # Call stop without await here, as we are inside the lock
                 # stop_docker_container needs to acquire the lock itself, so we schedule it
                 asyncio.create_task(stop_docker_container(code_block_id))


            running_containers[code_block_id] = {
                "container": container_obj,
                "stream_task": stream_task,
                "client_id": client_id,
                "websocket": websocket,
                "stop_event": stop_event # Store the stop event
            }

        # Wait for the container to finish execution or timeout
        print(f"Waiting for container {container_obj.short_id} ({code_block_id}) to finish...")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, # Use default executor
            lambda: container_obj.wait(timeout=DOCKER_TIMEOUT_SECONDS)
        )
        exit_code = result.get("StatusCode", -1)
        error_msg = result.get("Error", None)
        print(f"Container {container_obj.short_id} ({code_block_id}) finished. Result: {result}")

        # Ensure log streaming task is complete (it should be if container exited naturally)
        # Set stop event first to signal the thread, then wait/cancel task
        stop_event.set()
        try:
            # Wait briefly for the executor task to finish processing final logs
            await asyncio.wait_for(stream_task, timeout=5.0)
        except asyncio.TimeoutError:
            print(f"Warning: Log streaming task for {code_block_id} did not finish quickly after container exit.")
            # If it timed out, ensure it's cancelled (though setting stop_event should handle it)
            if not stream_task.done():
                stream_task.cancel()
        except asyncio.CancelledError:
             print(f"Log streaming task for {code_block_id} was cancelled (likely during stop).")
             pass # Already handled

        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id,
            "exit_code": exit_code,
            "error": error_msg # Send Docker-level error if any
        })

    except asyncio.TimeoutError: # Timeout from container_obj.wait()
        print(f"ERROR: Docker execution timed out for block {code_block_id} after {DOCKER_TIMEOUT_SECONDS}s.")
        await send_ws_message(websocket, "code_finished", {
             "code_block_id": code_block_id, "exit_code": -1,
             "error": f"Execution timed out after {DOCKER_TIMEOUT_SECONDS} seconds."
        })
        # Attempt to stop the timed-out container (this will also cancel the stream task via stop_docker_container)
        # Schedule stop_docker_container as it needs to acquire the lock
        asyncio.create_task(stop_docker_container(code_block_id))


    except ImageNotFound:
        error_msg = f"Server Error: Docker image '{image_name}' not found. Please pull it."
        print(f"ERROR for block {code_block_id}: {error_msg}")
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_msg})
    except APIError as e:
        error_msg = f"Server Error: Docker API error: {e}"
        print(f"ERROR for block {code_block_id}: {error_msg}")
        traceback.print_exc()
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_msg})
    except Exception as e:
        error_msg = f"Server Execution Error: {str(e)}"
        print(f"ERROR during Docker execution setup or wait for block {code_block_id}: {e}")
        traceback.print_exc()
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_msg})
        # Ensure stream task is cancelled if it was started
        if stream_task and not stream_task.done():
            print(f"Cancelling stream task for {code_block_id} due to setup/wait error.")
            stop_event.set() # Signal thread to stop
            stream_task.cancel()

    finally:
        # --- Final Cleanup ---
        # Remove from tracking dict if it hasn't been removed by stop_docker_container already
        async with running_containers_lock:
            if code_block_id in running_containers:
                print(f"Performing final cleanup check for {code_block_id}")
                task_to_cancel = running_containers[code_block_id]["stream_task"]
                stop_ev = running_containers[code_block_id]["stop_event"]
                if task_to_cancel and not task_to_cancel.done():
                     print(f"Final cleanup: Cancelling stream task for {code_block_id}")
                     stop_ev.set() # Signal thread
                     task_to_cancel.cancel()
                # Remove from tracking dict
                del running_containers[code_block_id]
                print(f"Removed {code_block_id} from running_containers during final cleanup")

        # Remove container if it exists and hasn't been removed by stop
        if container_obj:
            try:
                # Check if container still exists before removing
                await asyncio.get_running_loop().run_in_executor(None, container_obj.reload)
                print(f"Removing container {container_obj.short_id} ({code_block_id}) in final cleanup")
                # Need to run blocking remove in executor
                await asyncio.get_running_loop().run_in_executor(None, lambda: container_obj.remove(force=True))
            except NotFound:
                 print(f"Container {container_obj.short_id} already removed.")
                 pass # Already removed
            except Exception as rm_err:
                print(f"Error removing container {container_obj.short_id} in final cleanup: {rm_err}")

        # Clean up temporary directory
        if tmpdir_obj:
             try:
                  tmpdir_obj.cleanup()
             except Exception as cleanup_err:
                  print(f"Error cleaning up temp directory {tmpdir}: {cleanup_err}")


async def stop_docker_container(code_block_id: str):
    """Stops a running Docker container and cancels its log stream task."""
    global running_containers, running_containers_lock
    print(f"Attempting to stop execution for code block: {code_block_id}")
    loop = asyncio.get_running_loop()
    container_info = None
    container_obj = None
    stream_task = None
    websocket = None
    stop_event = None

    async with running_containers_lock:
        if code_block_id in running_containers:
            # Pop the entry to prevent others from trying to stop it simultaneously
            container_info = running_containers.pop(code_block_id)
            container_obj = container_info["container"]
            stream_task = container_info["stream_task"]
            websocket = container_info["websocket"] # Get websocket for final message
            stop_event = container_info["stop_event"]
            print(f"Found and removed running container {container_obj.short_id} for {code_block_id} from tracking.")
        else:
            print(f"Stop request for {code_block_id}, but it was not found in running_containers.")
            return # Nothing to stop

    # Perform actions outside the lock

    # 1. Signal the streaming thread to stop and cancel the task
    if stop_event:
        print(f"Setting stop event for {code_block_id}")
        stop_event.set()
    if stream_task and not stream_task.done():
        print(f"Cancelling stream task for {code_block_id}")
        stream_task.cancel()
        try:
            # Wait briefly for cancellation to be processed by the task
            await asyncio.wait_for(stream_task, timeout=1.0)
        except asyncio.TimeoutError:
            print(f"Stream task for {code_block_id} did not cancel quickly.")
        except asyncio.CancelledError:
            print(f"Stream task for {code_block_id} cancelled successfully.")
            pass # Expected

    # 2. Stop the container
    if container_obj:
        try:
            print(f"Stopping container {container_obj.short_id} ({code_block_id})...")
            # Stop needs to run in executor
            await loop.run_in_executor(None, lambda: container_obj.stop(timeout=5))
            print(f"Container {container_obj.short_id} stopped.")
        except Exception as stop_err:
            print(f"Error stopping container {container_obj.short_id}, attempting kill: {stop_err}")
            try:
                # Kill needs to run in executor
                await loop.run_in_executor(None, container_obj.kill)
                print(f"Container {container_obj.short_id} killed.")
            except Exception as kill_err:
                # Ignore kill error if container is already gone
                if "No such container" not in str(kill_err):
                     print(f"Error killing container {container_obj.short_id}: {kill_err}")

        # 3. Remove the container
        try:
            print(f"Removing container {container_obj.short_id} ({code_block_id}) after stop request.")
            # Remove needs to run in executor
            await loop.run_in_executor(None, lambda: container_obj.remove(force=True))
        except NotFound:
             pass # Already removed
        except Exception as rm_err:
            print(f"Error removing container {container_obj.short_id} after stop: {rm_err}")

    # 4. Send final message to client if websocket is still valid
    if websocket and websocket.client_state == WebSocketState.CONNECTED:
        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id,
            "exit_code": -1, # Indicate abnormal termination
            "error": "Execution stopped by user."
        })
    else:
         print(f"Cannot send stop confirmation for {code_block_id}, WebSocket reference lost or disconnected.")


async def cleanup_client_containers(client_id: str):
    """Stops and cleans up all running containers associated with a client ID."""
    global running_containers, running_containers_lock
    print(f"Cleaning up containers for disconnected client: {client_id}")
    containers_to_stop = []
    # Create a separate list to avoid modifying dict while iterating
    async with running_containers_lock:
         # Find code_block_ids associated with the client
         ids_for_client = [cb_id for cb_id, info in running_containers.items() if info["client_id"] == client_id]
         containers_to_stop.extend(ids_for_client)

    # Stop each container outside the lock to avoid holding it too long
    if containers_to_stop:
         print(f"Found containers to stop for client {client_id}: {containers_to_stop}")
         # Use asyncio.gather to stop them concurrently
         # Note: stop_docker_container already removes the entry from running_containers
         stop_tasks = [stop_docker_container(cb_id) for cb_id in containers_to_stop]
         results = await asyncio.gather(*stop_tasks, return_exceptions=True) # Log exceptions if any stop fails
         for i, result in enumerate(results):
              if isinstance(result, Exception):
                   print(f"Error during cleanup stop for {containers_to_stop[i]}: {result}")
         print(f"Finished cleanup for client {client_id}")
    else:
         print(f"No running containers found for client {client_id} during cleanup.")

# --- FastAPI Routes ---

# Root route to serve the main HTML page
@app.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    """Serves the main chat HTML page."""
    html_file_path = static_dir / "index.html"
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
    """Handles WebSocket connections for chat and code execution."""
    await websocket.accept()
    print(f"WebSocket connection accepted for client: {client_id}")
    memory = get_memory_for_client(client_id)

    # Function to load history using this client's memory (Unchanged)
    def load_memory_for_current_client(_):
        loaded_vars = memory.load_memory_variables({})
        return loaded_vars.get("history", [])

    # Simple chain using the fixed global 'prompt' (Unchanged)
    chain = (
        RunnablePassthrough.assign(history=load_memory_for_current_client)
        | prompt
        | model
        | output_parser
    )
    print(f"LCEL Chain created for client: {client_id}")

    try:
        while True:
            # Check state before receiving
            if websocket.client_state != WebSocketState.CONNECTED:
                 print(f"WebSocket no longer connected for {client_id} before receive. Breaking loop.")
                 break

            received_data = await websocket.receive_text()

            # Check if it's a JSON command or regular chat input
            try:
                message_data = json.loads(received_data)
                message_type = message_data.get("type")
                payload = message_data.get("payload")

                if message_type and payload and isinstance(payload, dict):
                    # --- Handle JSON Commands ---
                    code_block_id = payload.get("code_block_id")
                    if not code_block_id:
                         print(f"Received JSON command without code_block_id: {message_data}")
                         continue

                    if message_type == "run_code":
                        language = payload.get("language")
                        code = payload.get("code")
                        if language and code is not None:
                             print(f"Received 'run_code' request for block {code_block_id} ({language}) from {client_id}")
                             # Start execution in background task
                             asyncio.create_task(
                                 run_code_in_docker_stream(websocket, client_id, code_block_id, language, code)
                             )
                        else:
                             print(f"Invalid 'run_code' payload received: {payload}")
                             await send_ws_message(websocket, "code_finished", {
                                  "code_block_id": code_block_id, "exit_code": -1,
                                  "error": "Invalid run_code request payload from client."
                             })

                    elif message_type == "stop_code":
                        print(f"Received 'stop_code' request for block {code_block_id} from {client_id}")
                        # Stop execution in background task
                        asyncio.create_task(
                             stop_docker_container(code_block_id)
                        )

                    else:
                        print(f"Received unknown JSON command type '{message_type}' from {client_id}")

                else:
                     # Treat as chat if JSON structure is invalid
                     print(f"Received invalid JSON structure from {client_id}, treating as chat: {received_data[:100]}...")
                     await handle_chat_message(chain, memory, websocket, client_id, received_data)

            except json.JSONDecodeError:
                # --- Handle Regular Chat Message ---
                # print(f"Handling text message from {client_id}: '{received_data[:50]}...'")
                await handle_chat_message(chain, memory, websocket, client_id, received_data)

    except WebSocketDisconnect:
        print(f"WebSocket disconnected during receive/process for client: {client_id}")
    except Exception as e:
        print(f"Unknown Error in WebSocket loop for client {client_id}: {e}")
        traceback.print_exc()
    finally:
        # --- Cleanup on Disconnect or Error ---
        print(f"Cleaning up resources for client: {client_id}")
        remove_memory_for_client(client_id)
        # Stop any running containers for this client
        await cleanup_client_containers(client_id)
        # Attempt to close websocket gracefully if it's not already closed
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=1000) # Normal closure
                print(f"WebSocket closed gracefully for {client_id}")
            except Exception as close_exc:
                # Ignore errors if already closed or cannot close
                 print(f"Ignoring error during explicit WebSocket close for {client_id}: {close_exc}")
                 pass
        else:
             print(f"WebSocket already closed for {client_id} during cleanup.")


async def handle_chat_message(chain, memory, websocket: WebSocket, client_id: str, user_input: str):
     """Handles processing and streaming response for a regular chat message."""
     print(f"Handling chat message from {client_id}: '{user_input[:50]}...'")
     full_response = ""
     try:
         # Stream response using the chain
         async for chunk in chain.astream({"input": user_input}):
             # Check connection before sending each chunk
             if websocket.client_state != WebSocketState.CONNECTED:
                  print(f"WebSocket disconnected during chat stream for {client_id}. Aborting send.")
                  return # Stop sending if disconnected
             await websocket.send_text(chunk)
             full_response += chunk
     except Exception as chain_exc:
         error_msg = f"<ERROR>Error processing message: {chain_exc}"
         print(f"ERROR during chain execution for {client_id}: {chain_exc}")
         traceback.print_exc()
         # Try sending error message only if connected
         if websocket.client_state == WebSocketState.CONNECTED:
              await websocket.send_text(error_msg)
         # Don't save context if chain failed, but allow next message
         return

     # Send End Of Stream marker for chat message only if connected
     if websocket.client_state == WebSocketState.CONNECTED:
          await websocket.send_text("<EOS>")
          print(f"Finished streaming chat response to {client_id}")
          # Save context to memory
          # Note: user_input might contain the NO_THINK_PREFIX, which is fine for memory
          memory.save_context({"input": user_input}, {"output": full_response})
          print(f"Saved chat context to memory for client: {client_id}")
     else:
          print(f"WebSocket disconnected before sending <EOS> for {client_id}.")


# --- Function to run the server ---
def start_server():
    """Starts the Uvicorn server and opens the browser."""
    host = "127.0.0.1"
    port = 8001
    url = f"http://{host}:{port}"
    print(f"Starting server at {url}...")
    print(f"Using Ollama base URL: {OLLAMA_BASE_URL}")
    print(f"Using Model ID: {MODEL_ID}")
    print(f"Static files served from: {static_dir}")
    print(f"Supported execution languages: {list(SUPPORTED_LANGUAGES.keys())}")
    if not docker_client:
        print("WARNING: Docker client unavailable. Code execution will fail.")

    print(f"Attempting to open browser at {url}...")
    try:
        webbrowser.open(url)
    except Exception as browser_err:
        print(f"Warning: Could not automatically open browser: {browser_err}")
        print(f"Please navigate to {url} manually.")

    # --- Uvicorn Run ---
    # IMPORTANT: The target 'app.main:app' assumes this script (main.py)
    # is located inside a directory named 'app' relative to where you
    # run the uvicorn command OR that you run python like: python -m app.main
    # If main.py is at the project root, change the target to "main:app"
    uvicorn_target = "app.main:app"
    # Check if running directly (e.g., python main.py) vs module (python -m app.main)
    # A simple check: if the script's directory is named 'app'
    if Path(__file__).parent.name == "app":
         print(f"Running Uvicorn with target: '{uvicorn_target}' (assuming script is in 'app' directory)")
    else:
         # If not in 'app', assume it's at the root
         uvicorn_target = "main:app"
         print(f"Running Uvicorn with target: '{uvicorn_target}' (assuming script is at project root)")

    uvicorn.run(uvicorn_target, host=host, port=port, log_level="info", reload=True)

if __name__ == "__main__":
    # Add basic check for Docker client availability at startup
    if docker_client is None:
         print("\n---")
         print("WARNING: Docker is not running or accessible.")
         print("Code execution features will be disabled.")
         print("Please start Docker and restart this application for code execution.")
         print("---\n")
    start_server()

