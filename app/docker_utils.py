import asyncio
import docker
import struct
import traceback
import socket
import sys
import re
import os

from pathlib import Path
from typing import Dict, Any, List
from fastapi import WebSocket
from docker.errors import DockerException

from . import config, state
from .utils import send_ws_message

# --- Docker Client Initialization ---
docker_client = None
try:
    docker_client = docker.from_env()
    docker_client.ping()
    print("✅ Connected to Docker daemon.")
except DockerException as e:
    print(f"❌ Could not connect to Docker daemon: {e}")
    docker_client = None

# --- Windows Pipe Compatibility ---
try:
    import pywintypes
    PIPE_ENDED_ERROR = pywintypes.error
except ImportError:
    class DummyPipeEndedError(Exception): pass
    PIPE_ENDED_ERROR = DummyPipeEndedError

# --- Helpers ---

def get_docker_client():
    return docker_client

def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def detect_input_prompt(chunk_str: str) -> bool:
    stripped = chunk_str.rstrip('\n\r')
    return chunk_str == stripped and (
        stripped.endswith(': ') or stripped.endswith('> ') or stripped.endswith('? ') or
        'input' in stripped.lower() or 'enter' in stripped.lower()
    )

def prepare_run_command(project_path: str, lang_config: Dict[str, Any]) -> List[str]:
    return ["sh", "run.sh"]

async def stream_output(websocket: WebSocket, project_id: str, container, socket_obj):
    raw_sock = socket_obj._sock if hasattr(socket_obj, "_sock") else socket_obj
    buffer = b''
    try:
        while True:
            try:
                raw = await asyncio.to_thread(raw_sock.recv, 1024)
                if not raw:
                    break
            except (ConnectionResetError, BrokenPipeError, OSError, PIPE_ENDED_ERROR) as e:
                if isinstance(e, PIPE_ENDED_ERROR) and e.args[0] == 109:
                    break
                print(f"[Stream-{project_id}] Error receiving socket data: {e}")
                break

            buffer += raw

            while len(buffer) >= 8:
                header = buffer[:8]
                stream_type, size = struct.unpack('>BxxxL', header)

                if len(buffer) < 8 + size:
                    break

                payload = buffer[8:8+size].decode('utf-8', 'replace')
                stream = "stdout" if stream_type == 1 else "stderr"

                await send_ws_message(websocket, "code_output", {
                    "project_id": project_id,
                    "stream": stream,
                    "data": payload
                })

                if stream == 'stdout' and detect_input_prompt(payload):
                    await send_ws_message(websocket, "code_waiting_input", {
                        "project_id": project_id,
                        "prompt": payload
                    })

                buffer = buffer[8 + size:]

    except Exception as e:
        print(f"[Stream-{project_id}] Fatal error: {e}")
        traceback.print_exc()


def _sync_stream_and_wait(container, socket_obj, project_id, loop, websocket):
    raw_sock = socket_obj._sock if hasattr(socket_obj, "_sock") else socket_obj
    full_output = []
    try:
        while True:
            try:
                raw_data = raw_sock.recv(1024)
                if not raw_data:
                    break

                payload = raw_data.decode('utf-8', 'replace')
                full_output.append(payload)

                asyncio.run_coroutine_threadsafe(
                    send_ws_message(websocket, "code_output", {
                        "project_id": project_id,
                        "stream": "stdout",
                        "data": payload
                    }),
                    loop
                )

                if detect_input_prompt(payload):
                    asyncio.run_coroutine_threadsafe(
                        send_ws_message(websocket, "code_waiting_input", {
                            "project_id": project_id,
                            "prompt": payload
                        }),
                        loop
                    )
            except (ConnectionResetError, BrokenPipeError, OSError, PIPE_ENDED_ERROR) as e:
                if isinstance(e, PIPE_ENDED_ERROR) and e.args[0] == 109:
                    break
                print(f"[StreamSync-{project_id}] Socket error: {e}")
                break
    finally:
        print(f"[StreamSync-{project_id}] Stream finished. Waiting for container exit code.")
        result = container.wait()
        exit_code = result.get("StatusCode", 0)
        return exit_code, "".join(full_output)

async def run_code_in_docker(websocket: WebSocket, client_id: str, project_id: str,
                             project_data: Dict[str, Any], project_path: str,
                             run_command: str, lang_config: Dict[str, Any]):
    client = get_docker_client()
    if not client:
        return -1, None, "Docker is not available."

    host_base_path = os.environ.get('HOST_PROJECT_PATH')
    if not host_base_path:
        return -1, None, "Configuration error: HOST_PROJECT_PATH is not set."

    container = None
    try:
        command_to_run = prepare_run_command(project_path, lang_config)
        absolute_host_path_for_volume = os.path.join(host_base_path, project_path)

        container_env = {}
        if lang_config.get("language") == "python":
            container_env["PYTHONUNBUFFERED"] = "1"

        container = await asyncio.to_thread(
            client.containers.create,
            image=lang_config["image"],
            command=command_to_run,
            volumes={absolute_host_path_for_volume: {'bind': '/app', 'mode': 'rw'}},
            working_dir="/app",
            environment=container_env,
            mem_limit=config.DOCKER_MEM_LIMIT,
            stdin_open=True,
            tty=True,
            detach=True,
            labels={"managed-by": "tesseracs-chat"}
        )

        socket_obj = await asyncio.to_thread(container.attach_socket, params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        await asyncio.to_thread(container.start)

        async with state.running_containers_lock:
            state.running_containers[project_id] = {"container": container, "client_id": client_id, "socket": socket_obj}

        current_loop = asyncio.get_running_loop()

        exit_code, full_output = await asyncio.to_thread(
            _sync_stream_and_wait, container, socket_obj, project_id, current_loop, websocket
        )
        return exit_code, full_output, None

    except docker.errors.ImageNotFound:
        return -1, None, f"Docker image '{lang_config['image']}' not found."
    except Exception as e:
        traceback.print_exc()
        return -1, None, f"Execution failed: {e}"
    finally:
        await stop_container(project_id)

async def send_input_to_container(project_id: str, user_input: str):
    async with state.running_containers_lock:
        container_info = state.running_containers.get(project_id)

    if container_info and (sock := container_info.get("socket")):
        raw_sock = sock._sock if hasattr(sock, "_sock") else sock
        try:
            data = user_input.encode("utf-8")
            if not user_input.endswith("\n"):
                data += b"\n"
            await asyncio.to_thread(raw_sock.sendall, data)
        except Exception as e:
            print(f"[Input-{project_id}] Error sending input: {e}")

# --- Cleanup ---
async def stop_container(project_id: str):
    async with state.running_containers_lock:
        info = state.running_containers.pop(project_id, None)
    if info:
        try:
            if (sock := info.get("socket")):
                sock.close()
            container = info["container"]
            await asyncio.to_thread(container.stop, timeout=5)
            await asyncio.to_thread(container.remove, force=True)
            print(f"[Cleanup-{project_id}] Container stopped and removed.")
        except Exception as e:
            print(f"[Cleanup-{project_id}] Error during cleanup: {e}")


async def handle_preview_server(websocket: WebSocket, project_id: str, project_path: str, run_command: str, lang_config: Dict[str, Any], local_docker_client):
    port_match = re.search(r'\b(\d{4,5})\b', run_command)
    container_port = int(port_match.group(1)) if port_match else 8000
    host_port = _find_free_port()
    port_mapping = {f'{container_port}/tcp': host_port}
    
    try:
        container = await asyncio.to_thread(
            local_docker_client.containers.run,
            image=lang_config["image"],
            command=run_command.split(),
            volumes={project_path: {'bind': '/app', 'mode': 'rw'}},
            working_dir='/app',
            ports=port_mapping,
            detach=True,
            remove=True,
            mem_limit=config.DOCKER_MEM_LIMIT
        )
        preview_url = f"http://127.0.0.1:{host_port}"
        async with state.running_previews_lock:
            state.running_previews[project_id] = {
                "container": container, 
                "url": preview_url
            }
        await send_ws_message(websocket, "project_preview_ready", {
            "project_id": project_id, 
            "url": preview_url
        })
    except Exception as e:
        await send_ws_message(websocket, "code_finished", {
            "project_id": project_id, 
            "error": f"Failed to start preview: {e}"
        })


async def scavenge_orphaned_containers():
    """
    Compares the list of known running containers with all containers managed by this
    app and removes any that are orphaned.
    """
    client = get_docker_client()
    if not client:
        return # Docker not available

    try:
        # Get all container IDs that the application thinks are running
        async with state.running_containers_lock:
            known_container_ids = {info['container'].id for info in state.running_containers.values()}

        # Get all containers on the system managed by this application
        filter = {"label": "managed-by=tesseracs-chat"}
        all_managed_containers = await asyncio.to_thread(client.containers.list, all=True, filters=filter)

        orphaned_count = 0
        for container in all_managed_containers:
            if container.id not in known_container_ids:
                orphaned_count += 1
                print(f"🧹 SCAVENGER: Found orphaned container {container.short_id}. Removing...")
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception as e:
                    print(f"  -> SCAVENGER: Error removing container {container.short_id}: {e}")
        
        if orphaned_count > 0:
            print(f"✅ SCAVENGER: Cleanup of {orphaned_count} container(s) complete.")

    except Exception as e:
        print(f"❌ SCAVENGER: An error occurred during scavenging: {e}")


async def background_scavenger_task(interval_seconds: int):
    """
    A background task that periodically runs the scavenger function.
    """
    print(f" scavenger started. Will run every {interval_seconds} seconds.")
    while True:
        await asyncio.sleep(interval_seconds)
        print("SCAVENGER: Running periodic check for orphaned containers...")
        await scavenge_orphaned_containers()

# In docker_utils.py

async def cleanup_dangling_containers():
    """
    Finds and removes any containers from previous sessions that were
    left running, identified by a specific label.
    """
    client = get_docker_client()
    if not client:
        print("DOCKER_CLEANUP: Docker is not available, skipping cleanup.")
        return

    try:
        # Find all containers (running or stopped) with our custom label
        filter = {"label": "managed-by=tesseracs-chat"}
        orphaned_containers = await asyncio.to_thread(client.containers.list, all=True, filters=filter)

        if not orphaned_containers:
            print("✅ DOCKER_CLEANUP: No orphaned containers found.")
            return

        print(f"🧹 DOCKER_CLEANUP: Found {len(orphaned_containers)} orphaned container(s). Removing them...")
        for container in orphaned_containers:
            try:
                print(f"  -> Removing container {container.short_id} ({container.name})...")
                await asyncio.to_thread(container.remove, force=True)
            except Exception as e:
                print(f"  -> Error removing container {container.short_id}: {e}")
        print("✅ DOCKER_CLEANUP: Finished cleanup.")

    except Exception as e:
        print(f"❌ DOCKER_CLEANUP: An error occurred during cleanup: {e}")