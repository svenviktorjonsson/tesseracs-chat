# app/docker_utils.py
import asyncio
import tempfile
import traceback
from pathlib import Path
import docker
from docker.errors import DockerException, ImageNotFound, APIError, NotFound
from docker.models.containers import Container
from fastapi import WebSocket
import time
import socket

# Import pywintypes if on Windows, otherwise ignore
try:
    import pywintypes
    PIPE_ENDED_ERROR = pywintypes.error
except ImportError:
    # Create a dummy exception class for non-Windows platforms
    class DummyPipeEndedError(Exception):
        pass
    PIPE_ENDED_ERROR = DummyPipeEndedError

from . import config
from . import state
from .utils import send_ws_message

docker_client = None
try:
    docker_client = docker.from_env()
    docker_client.ping()
    print("Successfully connected to Docker daemon.")
except DockerException as e:
    print(f"CRITICAL WARNING: Could not connect to Docker daemon: {e}")
    print("Code execution via Docker will be unavailable.")
    docker_client = None

def get_docker_client():
    return docker_client

async def read_from_socket_and_stream(
    loop: asyncio.AbstractEventLoop,
    websocket: WebSocket,
    code_block_id: str,
    container: Container,
    socket: socket.socket
):
    """
    Reads from the container's interactive socket, decodes the Docker stream
    protocol, buffers output to detect prompts, and forwards to the WebSocket.
    """
    import struct

    # Use a buffer to handle partial lines and multiple lines in one read
    buffer = b''
    try:
        while True:
            # 1. Read raw data from the socket
            try:
                raw_data = await asyncio.to_thread(socket.recv, 8192)
            except PIPE_ENDED_ERROR:
                print(f"[SocketStreamer-{code_block_id}] Container finished normally (pipe ended)")
                break
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                print(f"[SocketStreamer-{code_block_id}] Connection closed: {e}")
                break

            if not raw_data:
                print(f"[SocketStreamer-{code_block_id}] No more data, container finished")
                break

            buffer += raw_data

            # 2. Process the buffer to extract complete Docker stream frames
            while len(buffer) >= 8:
                # Unpack header to get size
                header_bytes = buffer[:8]
                stream_type, size = struct.unpack('>BxxxL', header_bytes)

                # Check if the full frame is in the buffer
                if len(buffer) < 8 + size:
                    break # Wait for more data

                # Extract the payload
                payload_bytes = buffer[8 : 8 + size]

                # Remove the processed frame from the buffer
                buffer = buffer[8 + size:]

                # Decode and send to WebSocket
                stream_name = 'stdout' if stream_type == 1 else 'stderr'
                chunk_str = payload_bytes.decode('utf-8', 'replace')

                await send_ws_message(websocket, "code_output", {
                    "code_block_id": code_block_id, "stream": stream_name, "data": chunk_str
                })

                # Check for input prompts only on stdout
                if stream_name == 'stdout':
                    # The last part of the output might be an input prompt without a newline
                    # A simple heuristic: check if the chunk ends with common prompt indicators.
                    stripped_chunk = chunk_str.rstrip()
                    if stripped_chunk and stripped_chunk.endswith((':', '?', '>')):
                        print(f"[SocketStreamer-{code_block_id}] Detected input prompt: '{chunk_str}'")
                        await send_ws_message(websocket, "code_waiting_input", {"code_block_id": code_block_id, "prompt": chunk_str})

    except Exception as e:
        print(f"[SocketStreamer-{code_block_id}] Unexpected error in stream: {e}")
        traceback.print_exc()
    finally:
        print(f"[SocketStreamer-{code_block_id}] Stream finished - entering finally block")
        try:
            # This block remains the same, handling the final exit code
            print(f"[SocketStreamer-{code_block_id}] Reloading container to check status...")
            await asyncio.to_thread(container.reload)
            container_status = container.status
            print(f"[SocketStreamer-{code_block_id}] Container status: {container_status}")

            if container_status == 'running':
                print(f"[SocketStreamer-{code_block_id}] Container still running, getting state without waiting...")
                result = {"StatusCode": 0, "Error": None}
            else:
                print(f"[SocketStreamer-{code_block_id}] Container already finished, getting exit code...")
                container_state = container.attrs.get('State', {})
                exit_code = container_state.get('ExitCode', 0)
                error_detail = container_state.get('Error')
                result = {"StatusCode": exit_code, "Error": error_detail}
                print(f"[SocketStreamer-{code_block_id}] Exit code: {exit_code}, Error: {error_detail}")

            exit_code = result.get("StatusCode", 0)
            error_msg = result.get("Error")

            print(f"[SocketStreamer-{code_block_id}] About to send code_finished with exit_code: {exit_code}")

            if websocket.client_state.name != 'CONNECTED':
                print(f"[SocketStreamer-{code_block_id}] WARNING: WebSocket not connected (state: {websocket.client_state.name})")

            await send_ws_message(websocket, "code_finished", {
                "code_block_id": code_block_id, 
                "exit_code": exit_code, 
                "error": error_msg
            })
            print(f"[SocketStreamer-{code_block_id}] ✓ code_finished message sent successfully")

        except Exception as e:
            print(f"[SocketStreamer-{code_block_id}] ERROR in finally block: {e}")
            traceback.print_exc()
            try:
                await send_ws_message(websocket, "code_finished", {
                    "code_block_id": code_block_id, 
                    "exit_code": 0, 
                    "error": None
                })
                print(f"[SocketStreamer-{code_block_id}] ✓ Fallback code_finished message sent")
            except Exception as fallback_error:
                print(f"[SocketStreamer-{code_block_id}] ✗ Even fallback message failed: {fallback_error}")

        try:
            socket.close()
            print(f"[SocketStreamer-{code_block_id}] Socket closed")
        except Exception as socket_error:
            print(f"[SocketStreamer-{code_block_id}] Error closing socket: {socket_error}")

async def run_code_in_docker_stream(websocket: WebSocket, client_id: str, code_block_id: str, language: str, code: str):
    print(f"[DockerRun-{code_block_id}] === STARTING DOCKER CODE EXECUTION ===")
    
    local_docker_client = get_docker_client()
    if not local_docker_client:
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": "Docker service is unavailable."})
        return

    lang_config = config.SUPPORTED_LANGUAGES.get(language.lower())
    if not lang_config:
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": f"Language '{language}' not supported."})
        return

    tmpdir_obj = tempfile.TemporaryDirectory()
    tmp_path = Path(tmpdir_obj.name)
    container = None

    try:
        (tmp_path / lang_config["filename"]).write_text(code, encoding="utf-8")
        
        is_interactive = lang_config.get("interactive", True)
        
        container = local_docker_client.containers.run(
            image=lang_config["image"],
            command=lang_config["command"],
            volumes={str(tmp_path): {'bind': '/app', 'mode': 'rw'}},
            working_dir='/app',
            mem_limit=config.DOCKER_MEM_LIMIT,
            stdin_open=True,
            tty=False,
            detach=True,
            remove=False
        )

        if is_interactive:
            params = {'stdin': 1, 'stdout': 1, 'stderr': 1, 'stream': 1}
            # On Windows, this is an NpipeSocket; on Linux, a regular socket.
            # We use it directly without accessing ._sock
            interactive_socket = container.attach_socket(params=params)

            async with state.running_containers_lock:
                state.running_containers[code_block_id] = {
                    "container": container, "client_id": client_id, "socket": interactive_socket
                }

            loop = asyncio.get_running_loop()
            reader_task = loop.create_task(
                read_from_socket_and_stream(loop, websocket, code_block_id, container, interactive_socket)
            )
            await asyncio.wait_for(reader_task, timeout=config.DOCKER_TIMEOUT_SECONDS)

        else: # Batch mode for non-interactive
            result = container.wait(timeout=config.DOCKER_TIMEOUT_SECONDS)
            exit_code = result.get("StatusCode", 0)
            error_msg = result.get("Error")
            output = container.logs(stdout=True, stderr=True).decode('utf-8', errors='replace')
            await send_ws_message(websocket, "code_output", {"code_block_id": code_block_id, "stream": "stdout", "data": output})
            await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": exit_code, "error": error_msg})

    except asyncio.TimeoutError:
        print(f"[DockerRun-{code_block_id}] Session timed out after {config.DOCKER_TIMEOUT_SECONDS}s.")
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": 137, "error": "Execution timed out."})
    except Exception as e:
        error_payload = f"Server Execution Error: {e}"
        print(f"[DockerRun-{code_block_id}] CRITICAL ERROR: {error_payload}")
        traceback.print_exc()
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_payload})
    finally:
        print(f"[DockerRun-{code_block_id}] === CLEANUP PHASE ===")
        await stop_docker_container(code_block_id)
        if tmpdir_obj:
            try:
                tmpdir_obj.cleanup()
            except Exception as e:
                print(f"[DockerRun-{code_block_id}] Error cleaning temp directory: {e}")
        
        print(f"[DockerRun-{code_block_id}] === DOCKER EXECUTION COMPLETE ===")

async def send_input_to_container(code_block_id: str, user_input: str):
    """
    Finds the interactive socket for a container and sends user input to it.
    """
    async with state.running_containers_lock:
        container_info = state.running_containers.get(code_block_id)
        if container_info and (socket := container_info.get("socket")):
            try:
                # Use a thread to send data on the blocking socket
                await asyncio.to_thread(socket.sendall, user_input.encode('utf-8'))
                print(f"Sent input to {code_block_id}: {user_input.strip()}")
            except Exception as e:
                print(f"Error sending input via socket: {e}")
        else:
            print(f"Send input failed: No active container or socket for {code_block_id}")

async def stop_docker_container(code_block_id: str):
    container_info = None
    async with state.running_containers_lock:
        container_info = state.running_containers.pop(code_block_id, None)
    
    if not container_info:
        return

    if socket := container_info.get("socket"):
        try:
            socket.close()
            print(f"Socket for {code_block_id} closed.")
        except Exception as e_sock:
            print(f"Error closing socket for {code_block_id}: {e_sock}")

    if container := container_info.get("container"):
        try:
            # Check if container is still running before trying to kill it
            await asyncio.to_thread(container.reload)
            if container.status == 'running':
                await asyncio.to_thread(container.kill)
                print(f"Container for {code_block_id} killed.")
            else:
                print(f"Container for {code_block_id} already stopped (status: {container.status}).")
            
            # Always try to remove the container
            await asyncio.to_thread(container.remove, force=True)
            print(f"Container for {code_block_id} removed.")
        except Exception as e:
            print(f"Error stopping/removing container for {code_block_id}: {e}")

async def cleanup_client_containers(client_id: str):
    async with state.running_containers_lock:
        ids_for_client = [cb_id for cb_id, info in state.running_containers.items() if info.get("client_id") == client_id]
    
    if ids_for_client:
        await asyncio.gather(*[stop_docker_container(cb_id) for cb_id in ids_for_client], return_exceptions=True)