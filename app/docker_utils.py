import asyncio
import docker
import os
import shutil
import socket
import traceback
from pathlib import Path
from typing import Dict, Any, List
from multiprocessing import Process, Pipe
from multiprocessing.connection import Connection

from fastapi import WebSocket
from docker.errors import DockerException

from . import config, state
from .utils import send_ws_message
from .docker_worker import start_worker, STREAM_END_SIGNAL, ERROR_PREFIX

worker_process: Process | None = None
parent_conn: Connection | None = None

def start_docker_worker():
    global worker_process, parent_conn
    if worker_process is None or not worker_process.is_alive():
        print("DOCKER_UTILS: Starting Docker worker process...")
        parent_conn, child_conn = Pipe()
        worker_process = Process(target=start_worker, args=(child_conn,))
        worker_process.start()
        child_conn.close()
        print(f"DOCKER_UTILS: Worker process started with PID {worker_process.pid}.")

def shutdown_docker_worker():
    global worker_process, parent_conn
    print("DOCKER_UTILS: Shutting down Docker worker process...")
    if parent_conn:
        try:
            parent_conn.send("EXIT")
        except (BrokenPipeError, EOFError):
            pass
    if worker_process and worker_process.is_alive():
        worker_process.join(timeout=5)
        if worker_process.is_alive():
            worker_process.terminate()
            worker_process.join()
    print("DOCKER_UTILS: Worker process shut down.")

docker_client = None
try:
    docker_client = docker.from_env()
    docker_client.ping()
    print("✅ Connected to Docker daemon.")
except DockerException as e:
    print(f"❌ Could not connect to Docker daemon: {e}")
    docker_client = None

async def run_code_in_docker(websocket: WebSocket, client_id: str, project_id: str,
                             project_data: Dict[str, Any], project_path: str,
                             run_command: str, lang_config: Dict[str, Any], loop):
    if not parent_conn or not worker_process or not worker_process.is_alive():
        return -1, None, "Docker worker process is not running."

    job_payload = {
        "type": "start",
        "project_id": project_id,
        "project_path": project_path,
        "lang_config": lang_config,
    }

    queue = asyncio.Queue()
    container_id = None
    
    def pipe_data_received():
        try:
            data = parent_conn.recv()
            queue.put_nowait(data)
        except Exception as e:
            print(f"DOCKER_UTILS: Error reading from pipe: {e}")
            queue.put_nowait(STREAM_END_SIGNAL)
            
    pipe_fileno = parent_conn.fileno()
    loop.add_reader(pipe_fileno, pipe_data_received)

    full_output_parts = []
    exit_code = -1
    error_message = None

    try:
        await loop.run_in_executor(None, parent_conn.send, job_payload)

        while True:
            data = await queue.get()
            if data == STREAM_END_SIGNAL:
                break
            if isinstance(data, str) and data.startswith(ERROR_PREFIX):
                error_message = data[len(ERROR_PREFIX):]
                full_output_parts.append(error_message)
                break
            
            msg_type = data.get("type")
            if msg_type == "container_started":
                container_id = data.get("container_id")
                if container_id and docker_client:
                    try:
                        container_obj = docker_client.containers.get(container_id)
                        async with state.running_containers_lock:
                            state.running_containers[project_id] = {"container": container_obj, "client_id": client_id}
                    except Exception as e:
                         print(f"DOCKER_UTILS: Could not get container object for {container_id}: {e}")
            elif msg_type == "waiting_for_input":
                await send_ws_message(websocket, "code_waiting_input", {"project_id": project_id})
            elif msg_type == "chunk":
                payload = data.get("data", "")
                full_output_parts.append(payload)
                await send_ws_message(websocket, "code_output", {
                    "project_id": project_id,
                    "stream": data.get("stream"),
                    "data": payload
                })
            elif msg_type == "exit_code":
                exit_code = data.get("exit_code", -1)

    except Exception as e:
        error_message = f"An unexpected error occurred in run_code_in_docker: {e}"
        traceback.print_exc()
    finally:
        loop.remove_reader(pipe_fileno)
        async with state.running_containers_lock:
            state.running_containers.pop(project_id, None)

    return exit_code, "".join(full_output_parts), error_message

async def send_input_to_container(project_id: str, user_input: str):
    if not parent_conn:
        return False
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, parent_conn.send, {"type": "input", "data": user_input})
        return True
    except Exception as e:
        print(f"DOCKER_UTILS: Error sending input via pipe: {e}")
        return False

async def stop_container(project_id: str):
    async with state.running_containers_lock:
        info = state.running_containers.pop(project_id, None)
    
    if info and (container := info.get("container")):
        try:
            await asyncio.to_thread(container.kill)
            await asyncio.to_thread(container.remove, force=True)
        except docker.errors.NotFound:
            pass
        except Exception as e:
            print(f"[Cleanup-{project_id}] Error during container cleanup: {e}")
    
    async with state.running_previews_lock:
        preview_info = state.running_previews.pop(project_id, None)
    if preview_info:
        try:
            container = preview_info["container"]
            await asyncio.to_thread(container.kill)
            await asyncio.to_thread(container.remove, force=True)
            state.preview_routes.pop(project_id, None)
        except docker.errors.NotFound: pass 
        except Exception as e:
            print(f"[Cleanup-{project_id}] Error during preview container cleanup: {e}")

def _get_host_path_from_container_path(container_path: str) -> str:
    try:
        container_id = socket.gethostname()
        container = docker_client.containers.get(container_id)
        mounts = container.attrs['Mounts']
        for mount in sorted(mounts, key=lambda m: len(m['Destination']), reverse=True):
            container_mount_point = mount['Destination']
            host_mount_point = mount['Source']
            if container_path.startswith(container_mount_point):
                relative_path = os.path.relpath(container_path, container_mount_point)
                host_path = os.path.join(host_mount_point, relative_path)
                return host_path
    except Exception as e:
        print(f"[DOCKER_UTILS] ⚠️  Could not translate container path: {e}")
    return container_path

def get_current_container_network():
    if not docker_client: return None
    try:
        container_id = socket.gethostname()
        container = docker_client.containers.get(container_id)
        networks = container.attrs['NetworkSettings']['Networks']
        return next(iter(networks)) if networks else None
    except Exception as e:
        print(f"DOCKER_UTILS: Could not determine container's network: {e}")
    return None

async def handle_preview_server(websocket: WebSocket, project_id: str, persistent_project_id: str, project_path: str, lang_config: Dict[str, Any]):
    container_name = f"preview-{persistent_project_id}"
    try:
        existing_container = await asyncio.to_thread(docker_client.containers.get, container_name)
        await asyncio.to_thread(existing_container.remove, force=True)
    except docker.errors.NotFound: pass
    except Exception as e:
        await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": f"Failed to clean up old preview container: {e}."})
        return

    await stop_container(project_id)
    app_network = await asyncio.to_thread(get_current_container_network)
    if not app_network:
        await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": "Could not determine the application's Docker network."})
        return
    
    host_project_path = await asyncio.to_thread(_get_host_path_from_container_path, project_path)
    if host_project_path == project_path:
         await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": "Could not translate container project path to a host path for preview."})
         return

    try:
        container = await asyncio.to_thread(
            docker_client.containers.run,
            name=container_name, image=lang_config["image"], command=["sh", "run.sh"],
            volumes={host_project_path: {'bind': '/app', 'mode': 'rw'}},
            working_dir='/app', network=app_network, detach=True, remove=False,
            stdin_open=False, tty=False,
            mem_limit=config.DOCKER_MEM_LIMIT,
            labels={"managed-by": "tesseracs-chat-preview", "project_id": project_id}
        )
        
        internal_url = f"http://{container_name}:8000"
        proxy_path = f"/preview/{project_id}/"
        state.preview_routes[project_id] = internal_url
        
        async with state.running_previews_lock:
            state.running_previews[project_id] = { "container": container, "project_path": project_path, "client_id": websocket.scope.get("client_id") }

        await send_ws_message(websocket, "project_preview_ready", { "project_id": project_id, "url": proxy_path })
    except Exception as e:
        traceback.print_exc()
        await send_ws_message(websocket, "code_finished", { "project_id": project_id, "error": f"Failed to start preview server: {e}"})

async def cleanup_dangling_containers():
    if not docker_client: return
    try:
        filters = {"label": ["managed-by=tesseracs-chat", "managed-by=tesseracs-chat-preview"]}
        orphaned_containers = await asyncio.to_thread(docker_client.containers.list, all=True, filters=filters)
        if not orphaned_containers: return
        
        for container in orphaned_containers:
            try:
                await asyncio.to_thread(container.remove, force=True)
            except Exception: pass
    except Exception: pass

async def scavenge_orphaned_containers():
    if not docker_client: return
    try:
        async with state.running_containers_lock:
            known_container_ids = {info['container'].id for info in state.running_containers.values()}
        filters = {"label": "managed-by=tesseracs-chat"}
        all_managed_containers = await asyncio.to_thread(docker_client.containers.list, all=True, filters=filters)
        for container in all_managed_containers:
            if container.id not in known_container_ids:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception: pass
    except Exception: pass

async def background_scavenger_task(interval_seconds: int):
    while True:
        await asyncio.sleep(interval_seconds)
        await scavenge_orphaned_containers()