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

    line_buffer = ""
    try:
        while True:
            # 1. Read the 8-byte header from the Docker stream
            header_bytes = await asyncio.to_thread(socket.recv, 8)
            if not header_bytes:
                break
            if len(header_bytes) < 8:
                print(f"[SocketStreamer-{code_block_id}] Incomplete header received, closing stream.")
                break

            # 2. Unpack the header to get stream type and content size
            stream_type, size = struct.unpack('>BxxxL', header_bytes)
            
            # 3. Read the exact amount of data specified in the header
            payload_bytes = b''
            bytes_to_read = size
            while bytes_to_read > 0:
                chunk = await asyncio.to_thread(socket.recv, bytes_to_read)
                if not chunk:
                    raise OSError("Socket closed unexpectedly while reading payload")
                payload_bytes += chunk
                bytes_to_read -= len(chunk)

            stream_name = 'stdout' if stream_type == 1 else 'stderr'
            chunk_str = payload_bytes.decode('utf-8', 'replace')

            # The rest of the logic is the same as before
            await send_ws_message(websocket, "code_output", {
                "code_block_id": code_block_id, "stream": stream_name, "data": chunk_str
            })

            if stream_name == 'stdout':
                line_buffer += chunk_str
                if '\n' in line_buffer or '\r' in line_buffer:
                    line_buffer = ""
                    continue
                
                prompt_indicators = [':', '?', '>', 'input', 'enter']
                line_lower = line_buffer.lower().strip()
                if line_lower and any(indicator in line_lower for indicator in prompt_indicators):
                    print(f"[SocketStreamer-{code_block_id}] Detected input prompt: '{line_buffer}'")
                    await send_ws_message(websocket, "code_waiting_input", {"code_block_id": code_block_id, "prompt": line_buffer})
                    line_buffer = ""

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[SocketStreamer-{code_block_id}] Connection closed: {e}")
    except Exception as e:
        print(f"[SocketStreamer-{code_block_id}] Error in stream: {e}")
        traceback.print_exc()
    finally:
        print(f"[SocketStreamer-{code_block_id}] Stream finished.")
        try:
            result = container.wait(timeout=1)
            exit_code = result.get("StatusCode", 0)
            error_msg = result.get("Error")
            await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": exit_code, "error": error_msg})
        except Exception:
            await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": 0, "error": None})
        
        socket.close()

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
            await asyncio.to_thread(container.kill)
            await asyncio.to_thread(container.remove, force=True)
            print(f"Container for {code_block_id} stopped and removed.")
        except Exception as e:
            print(f"Error stopping/removing container for {code_block_id}: {e}")

async def cleanup_client_containers(client_id: str):
    async with state.running_containers_lock:
        ids_for_client = [cb_id for cb_id, info in state.running_containers.items() if info.get("client_id") == client_id]
    
    if ids_for_client:
        await asyncio.gather(*[stop_docker_container(cb_id) for cb_id in ids_for_client], return_exceptions=True)