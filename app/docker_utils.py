# app/docker_utils.py

import asyncio
import tempfile
import shutil
import traceback
from pathlib import Path
import docker # Docker SDK for Python
from docker.errors import DockerException, ImageNotFound, APIError, NotFound # Docker specific errors
from docker.models.containers import Container # For type hinting container objects
from fastapi import WebSocket # For type hinting WebSockets

# Relative imports for modules within the 'app' package
from . import config
from . import state
from .utils import send_ws_message # Utility function for sending WebSocket messages

# --- Docker Client Initialization ---
# Attempt to initialize the Docker client from the environment settings.
# This typically connects to the Docker daemon running locally.
docker_client = None
try:
    # Get Docker client from environment variables (DOCKER_HOST, etc.)
    docker_client = docker.from_env()
    # Ping the Docker daemon to ensure connectivity
    docker_client.ping()
    print("Successfully connected to Docker daemon.")
except DockerException as e:
    # Handle exceptions if the Docker daemon is not reachable
    print(f"CRITICAL WARNING: Could not connect to Docker daemon: {e}")
    print("Code execution via Docker will be unavailable.")
    docker_client = None # Ensure client is None if connection failed

def get_docker_client():
    """
    Returns the initialized Docker client instance.
    Returns None if the client failed to initialize.
    """
    return docker_client

# --- Log Streaming Logic (Designed to Run in Executor Thread) ---

def sync_log_streamer(container: Container, websocket: WebSocket, code_block_id: str, loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event):
    """
    Synchronously iterates through Docker container logs and schedules sending
    log lines back to the main asyncio event loop via WebSocket.
    This function is blocking and intended to be run in a separate thread using run_in_executor.

    Args:
        container: The Docker container object to stream logs from.
        websocket: The WebSocket connection to send log messages to.
        code_block_id: Identifier for the code block being executed.
        loop: The main asyncio event loop to schedule coroutines on.
        stop_event: An asyncio.Event used to signal when to stop streaming.
    """
    try:
        # Get a blocking generator for log lines (stdout and stderr)
        # follow=True keeps the stream open until the container stops or stop_event is set.
        log_stream = container.logs(stream=True, follow=True, stdout=True, stderr=True)
        print(f"[Thread-{code_block_id}] Starting log iteration for container {container.short_id}")

        # Iterate through the log stream line by line
        for line_bytes in log_stream:
            # Check if the stop event has been set (e.g., by user cancellation)
            if stop_event.is_set():
                print(f"[Thread-{code_block_id}] Stop event set, breaking log iteration.")
                break # Exit the loop if stop is requested

            # Decode bytes to string, replacing errors
            line_str = line_bytes.decode('utf-8', errors='replace')

            # Schedule the asynchronous send_ws_message function to run on the main event loop
            # This is crucial for thread safety when interacting with asyncio objects like WebSockets.
            asyncio.run_coroutine_threadsafe(
                send_ws_message(websocket, "code_output", {
                    "code_block_id": code_block_id,
                    "stream": "stdout", # Simplification: send both stdout/stderr as stdout type for now
                    "data": line_str
                }),
                loop # Pass the main event loop
            )

        print(f"[Thread-{code_block_id}] Finished log iteration for container {container.short_id}")

    except Exception as e:
        # Handle exceptions during log streaming (e.g., container removed unexpectedly)
        print(f"[Thread-{code_block_id}] Error during log streaming: {e}")
        # Schedule sending an error message back to the client via the main loop
        asyncio.run_coroutine_threadsafe(
            send_ws_message(websocket, "code_output", {
                "code_block_id": code_block_id,
                "stream": "error", # Indicate an error stream
                "data": f"\n[Error streaming logs from container: {str(e)}]"
            }),
            loop
        )

async def stream_docker_logs_via_executor(websocket: WebSocket, container: Container, code_block_id: str, stop_event: asyncio.Event):
    """
    Asynchronously runs the synchronous log streamer function (`sync_log_streamer`)
    in a separate thread using asyncio's default executor.

    Args:
        websocket: The WebSocket connection.
        container: The Docker container object.
        code_block_id: Identifier for the code block.
        stop_event: Event to signal stopping the stream.
    """
    loop = asyncio.get_running_loop()
    print(f"Scheduling log streaming task in executor for {code_block_id}")
    # Run the blocking sync_log_streamer function in the thread pool executor
    await loop.run_in_executor(
        None, # Use the default executor
        sync_log_streamer, # The function to run
        # Arguments to pass to sync_log_streamer:
        container, websocket, code_block_id, loop, stop_event
    )
    print(f"Executor task for log streaming finished for {code_block_id}")


# --- Docker Code Execution Core Function ---

async def run_code_in_docker_stream(websocket: WebSocket, client_id: str, code_block_id: str, language: str, code: str):
    """
    Handles the complete process of running user-provided code in a Docker container:
    1. Validates language and Docker availability.
    2. Creates a temporary directory and writes the code to a file.
    3. Starts the appropriate Docker container based on the language config.
    4. Starts a background task to stream container logs back via WebSocket.
    5. Waits for the container to finish or timeout.
    6. Sends a final status message (success, error, timeout) via WebSocket.
    7. Cleans up the container and temporary directory.

    Args:
        websocket: The client's WebSocket connection.
        client_id: The unique ID of the client.
        code_block_id: Identifier for the specific code block being run.
        language: The programming language of the code (e.g., "python", "c++").
        code: The actual code string provided by the user.
    """
    # Get the initialized Docker client instance
    local_docker_client = get_docker_client()

    # Check if Docker client is available
    if not local_docker_client:
        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id, "exit_code": -1,
            "error": "Docker service is unavailable on the server."
        })
        return

    # Validate the requested language against the configuration
    lang_key = language.lower()
    lang_config = config.SUPPORTED_LANGUAGES.get(lang_key)
    if not lang_config:
        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id, "exit_code": -1,
            "error": f"Language '{language}' not supported for execution."
        })
        return

    # Extract language-specific configuration
    image_name = lang_config["image"]
    script_filename = lang_config["filename"]
    command = lang_config["command"]

    # Initialize variables for container, task, temp dir, and stop event
    container_obj: Container | None = None
    stream_task: asyncio.Task | None = None
    tmpdir_obj = None # To hold the TemporaryDirectory object for proper cleanup
    stop_event = asyncio.Event() # Event to signal cancellation to the streamer thread

    try:
        # Create a temporary directory to store the script file
        # Using 'with' ensures cleanup even if errors occur later, but we need the path
        # outside the 'with', so we create it manually and clean up in 'finally'.
        tmpdir_obj = tempfile.TemporaryDirectory()
        tmpdir = tmpdir_obj.name # Get the path string
        host_script_path = Path(tmpdir) / script_filename

        # Write the user's code to the script file inside the temp directory
        with open(host_script_path, "w", encoding="utf-8") as f:
            f.write(code)

        print(f"Attempting to run block {code_block_id} ({lang_key}) in Docker image {image_name}...")

        # Run the Docker container
        # This might implicitly pull the image if not found locally
        container_obj = local_docker_client.containers.run(
            image=image_name,
            command=command, # Command defined in config.py
            # Mount the temporary directory read-write into /app inside the container
            # Changed mode from 'ro' to 'rw' to allow compilation output
            volumes={tmpdir: {'bind': '/app', 'mode': 'rw'}}, # <<< CHANGED HERE
            working_dir='/app', # Set working directory inside the container
            mem_limit=config.DOCKER_MEM_LIMIT, # Apply memory limit from config
            stdout=True, # Capture stdout
            stderr=True, # Capture stderr
            detach=True, # Run the container in the background
            # auto_remove=True, # Cannot auto-remove if we need logs/exit code/manual stop
        )
        print(f"Container {container_obj.short_id} started for {code_block_id}")

        # Create and start the background task for streaming logs
        stream_task = asyncio.create_task(
            stream_docker_logs_via_executor(websocket, container_obj, code_block_id, stop_event),
            name=f"log_stream_{code_block_id}" # Name the task for easier debugging
        )

        # Add the running container info to the global state dictionary (thread-safe)
        async with state.running_containers_lock:
            # Check if another execution for the same block ID is already running
            if code_block_id in state.running_containers:
                # This shouldn't happen if frontend disables button, but handle defensively
                print(f"WARNING: Code block {code_block_id} was already running. Stopping previous run.")
                # Schedule stop_docker_container; do not await inside the lock
                asyncio.create_task(stop_docker_container(code_block_id))

            # Store container details in the shared state dictionary
            state.running_containers[code_block_id] = {
                "container": container_obj,
                "stream_task": stream_task,
                "client_id": client_id,
                "websocket": websocket,
                "stop_event": stop_event # Store the stop event for cancellation
            }

        # Wait for the container to finish execution (blocking call run in executor)
        print(f"Waiting for container {container_obj.short_id} ({code_block_id}) to finish...")
        loop = asyncio.get_running_loop()
        # container.wait() is blocking, so run it in the executor
        result = await loop.run_in_executor(
            None, # Use default executor
            lambda: container_obj.wait(timeout=config.DOCKER_TIMEOUT_SECONDS) # Wait with timeout
        )
        # Extract exit code and potential error message from the result
        exit_code = result.get("StatusCode", -1) # Default to -1 if status code missing
        error_msg = result.get("Error", None) # Docker daemon error message
        print(f"Container {container_obj.short_id} ({code_block_id}) finished. Result: {result}")

        # --- Container Finished Normally or Errored ---
        # Signal the log streaming thread that it can stop
        stop_event.set()
        try:
            # Wait briefly for the log streaming task to finish processing final logs
            await asyncio.wait_for(stream_task, timeout=5.0)
        except asyncio.TimeoutError:
            print(f"Warning: Log streaming task for {code_block_id} did not finish quickly after container exit.")
            # Cancel the task if it's still running after the timeout
            if stream_task and not stream_task.done():
                stream_task.cancel()
        except asyncio.CancelledError:
            # Expected if the task was cancelled during stop_docker_container
             print(f"Log streaming task for {code_block_id} was cancelled (likely during stop).")

        # Send the final result message to the client
        await send_ws_message(websocket, "code_finished", {
            "code_block_id": code_block_id,
            "exit_code": exit_code,
            "error": error_msg # Send Docker-level error if any (e.g., OOMKilled)
        })

    except asyncio.TimeoutError: # Timeout triggered by container_obj.wait()
        # Handle the case where the container ran longer than DOCKER_TIMEOUT_SECONDS
        print(f"ERROR: Docker execution timed out for block {code_block_id} after {config.DOCKER_TIMEOUT_SECONDS}s.")
        await send_ws_message(websocket, "code_finished", {
             "code_block_id": code_block_id, "exit_code": -1,
             "error": f"Execution timed out after {config.DOCKER_TIMEOUT_SECONDS} seconds."
        })
        # Schedule the container stop process in the background
        asyncio.create_task(stop_docker_container(code_block_id))

    except ImageNotFound:
        # Handle case where the specified Docker image doesn't exist locally and couldn't be pulled
        error_msg = f"Server Error: Docker image '{image_name}' not found. Please ensure it's pulled or available."
        print(f"ERROR for block {code_block_id}: {error_msg}")
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_msg})
    except APIError as e:
        # Handle errors from the Docker daemon API
        error_msg = f"Server Error: Docker API error: {e}"
        print(f"ERROR for block {code_block_id}: {error_msg}")
        traceback.print_exc() # Print full traceback for server logs
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_msg})
    except Exception as e:
        # Catch any other unexpected errors during setup or execution
        error_msg = f"Server Execution Error: An unexpected error occurred." # Generic message for client
        print(f"ERROR during Docker execution setup or wait for block {code_block_id}: {e}")
        traceback.print_exc() # Print full traceback for server logs
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_msg})
        # Ensure stream task is cancelled if it was started before the error
        if stream_task and not stream_task.done():
            print(f"Cancelling stream task for {code_block_id} due to setup/wait error.")
            stop_event.set() # Signal thread to stop
            stream_task.cancel()

    finally:
        # --- Final Cleanup ---
        # This block executes whether the try block succeeded or failed

        # Remove container info from tracking dict if it hasn't been removed already
        async with state.running_containers_lock:
            if code_block_id in state.running_containers:
                print(f"Performing final cleanup check for {code_block_id}")
                # Retrieve task and event references before deleting the entry
                task_to_cancel = state.running_containers[code_block_id].get("stream_task")
                stop_ev = state.running_containers[code_block_id].get("stop_event")
                # Ensure task is stopped and cancelled if still running
                if task_to_cancel and not task_to_cancel.done():
                    print(f"Final cleanup: Cancelling stream task for {code_block_id}")
                    if stop_ev: stop_ev.set() # Signal thread
                    task_to_cancel.cancel()
                # Remove the entry from the tracking dictionary
                del state.running_containers[code_block_id]
                print(f"Removed {code_block_id} from running_containers during final cleanup")

        # Ensure the Docker container is removed
        if container_obj:
            try:
                # Need to run blocking Docker SDK calls in an executor thread
                loop = asyncio.get_running_loop()
                # Check if container still exists before trying to remove
                await loop.run_in_executor(None, container_obj.reload)
                print(f"Removing container {container_obj.short_id} ({code_block_id}) in final cleanup")
                # Force remove the container
                await loop.run_in_executor(None, lambda: container_obj.remove(force=True))
            except NotFound:
                # Container was already removed (e.g., by stop_docker_container)
                 print(f"Container {container_obj.short_id} already removed.")
            except Exception as rm_err:
                # Log error if removal fails for some other reason
                 print(f"Error removing container {container_obj.short_id} in final cleanup: {rm_err}")

        # Clean up the temporary directory
        if tmpdir_obj:
             try:
                 tmpdir_obj.cleanup() # Deletes the temporary directory and its contents
             except Exception as cleanup_err:
                 print(f"Error cleaning up temp directory {tmpdir_obj.name}: {cleanup_err}")


# --- Container Stopping Logic ---

async def stop_docker_container(code_block_id: str):
    """
    Stops a specific running Docker container identified by code_block_id.
    Handles cancelling the log stream task and removing the container.

    Args:
        code_block_id: The identifier of the code block whose container needs stopping.
    """
    print(f"Attempting to stop execution for code block: {code_block_id}")
    loop = asyncio.get_running_loop()
    container_info = None
    container_obj = None
    stream_task = None
    websocket = None
    stop_event = None

    # Safely access and remove the container info from the shared state
    async with state.running_containers_lock:
        if code_block_id in state.running_containers:
            # Pop the entry to prevent race conditions with other stop requests or cleanup
            container_info = state.running_containers.pop(code_block_id)
            container_obj = container_info.get("container")
            stream_task = container_info.get("stream_task")
            websocket = container_info.get("websocket") # Get websocket for final message
            stop_event = container_info.get("stop_event")
            print(f"Found and removed running container info for {code_block_id} from tracking.")
        else:
            # If not found, it might have finished or been stopped already
            print(f"Stop request for {code_block_id}, but it was not found in running_containers.")
            return # Nothing further to do

    # --- Perform actions outside the lock ---

    # 1. Signal the streaming thread to stop and cancel the asyncio task
    if stop_event:
        print(f"Setting stop event for {code_block_id}")
        stop_event.set()
    if stream_task and not stream_task.done():
        print(f"Cancelling stream task for {code_block_id}")
        stream_task.cancel()
        try:
            # Wait briefly for cancellation to be processed
            await asyncio.wait_for(stream_task, timeout=1.0)
        except asyncio.TimeoutError:
            print(f"Stream task for {code_block_id} did not cancel quickly.")
        except asyncio.CancelledError:
            print(f"Stream task for {code_block_id} cancelled successfully.")
            pass # Expected outcome

    # 2. Stop the Docker container (run blocking calls in executor)
    if container_obj:
        try:
            print(f"Stopping container {container_obj.short_id} ({code_block_id})...")
            # container.stop() is blocking
            await loop.run_in_executor(None, lambda: container_obj.stop(timeout=5))
            print(f"Container {container_obj.short_id} stopped.")
        except Exception as stop_err:
            # If stop fails (e.g., timeout), attempt to kill the container
            print(f"Error stopping container {container_obj.short_id}, attempting kill: {stop_err}")
            try:
                # container.kill() is blocking
                await loop.run_in_executor(None, container_obj.kill)
                print(f"Container {container_obj.short_id} killed.")
            except Exception as kill_err:
                # Ignore "No such container" error if already gone, log others
                 if "No such container" not in str(kill_err):
                     print(f"Error killing container {container_obj.short_id}: {kill_err}")

        # 3. Remove the Docker container (run blocking call in executor)
        try:
            print(f"Removing container {container_obj.short_id} ({code_block_id}) after stop request.")
            # container.remove() is blocking
            await loop.run_in_executor(None, lambda: container_obj.remove(force=True))
        except NotFound:
            pass # Container already removed
        except Exception as rm_err:
            print(f"Error removing container {container_obj.short_id} after stop: {rm_err}")

    # 4. Send final "stopped by user" message if WebSocket is still valid
    if websocket: # Check if websocket reference exists
         try:
             await send_ws_message(websocket, "code_finished", {
                 "code_block_id": code_block_id,
                 "exit_code": -1, # Indicate abnormal termination
                 "error": "Execution stopped by user."
             })
         except Exception as send_err:
              print(f"Error sending stop confirmation for {code_block_id}: {send_err}")
    else:
         print(f"Cannot send stop confirmation for {code_block_id}, WebSocket reference lost.")


# --- Client Disconnect Cleanup ---

async def cleanup_client_containers(client_id: str):
    """
    Stops and cleans up all running Docker containers associated with a specific client ID
    when that client disconnects.

    Args:
        client_id: The ID of the client that disconnected.
    """
    print(f"Cleaning up containers for disconnected client: {client_id}")
    containers_to_stop = []
    # Safely get a list of code_block_ids associated with the disconnected client
    async with state.running_containers_lock:
        # List comprehension to find matching client_id
        ids_for_client = [
            cb_id for cb_id, info in state.running_containers.items()
            if info.get("client_id") == client_id
        ]
        containers_to_stop.extend(ids_for_client)

    # Stop each container concurrently using asyncio.gather
    if containers_to_stop:
        print(f"Found containers to stop for client {client_id}: {containers_to_stop}")
        # Create a list of stop tasks
        stop_tasks = [stop_docker_container(cb_id) for cb_id in containers_to_stop]
        # Run tasks concurrently and gather results (including exceptions)
        results = await asyncio.gather(*stop_tasks, return_exceptions=True)
        # Log any errors that occurred during the cleanup stops
        for i, result in enumerate(results):
             if isinstance(result, Exception):
                 print(f"Error during cleanup stop for {containers_to_stop[i]}: {result}")
        print(f"Finished cleanup for client {client_id}")
    else:
        print(f"No running containers found for client {client_id} during cleanup.")

