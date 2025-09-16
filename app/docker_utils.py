# app/docker_utils.py

import asyncio
import traceback
import docker
from docker.errors import DockerException
from docker.models.containers import Container
from fastapi import WebSocket, HTTPException
import socket
import re
from typing import Dict, Any
from pathlib import Path
import json

from . import config
from . import state
from .utils import send_ws_message

# --- Docker Client Initialization ---
docker_client = None
try:
    docker_client = docker.from_env()
    docker_client.ping()
    print("Successfully connected to Docker daemon.")
except DockerException as e:
    print(f"CRITICAL WARNING: Could not connect to Docker daemon: {e}")
    docker_client = None

try:
    import pywintypes
    PIPE_ENDED_ERROR = pywintypes.error
except ImportError:
    # Create a dummy exception class if pywintypes is not available (for non-Windows)
    class DummyPipeEndedError(Exception):
        pass
    PIPE_ENDED_ERROR = DummyPipeEndedError

def get_docker_client():
    return docker_client

# --- Networking Helper ---
def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


async def read_from_socket_and_stream(
    loop: asyncio.AbstractEventLoop,
    websocket: WebSocket,
    project_id: str,
    container: Container,
    socket: socket.socket,
    timeout_seconds: int
):
    import struct
    buffer = b''
    try:
        while True:
            try:
                raw_data = await asyncio.wait_for(
                    asyncio.to_thread(socket.recv, 8192),
                    timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                print(f"[SocketStreamer-{project_id}] Inactivity timeout after {timeout_seconds}s.")
                raise
            except (ConnectionResetError, BrokenPipeError, OSError, PIPE_ENDED_ERROR) as e:
                print(f"[SocketStreamer-{project_id}] Connection closed normally: {e}")
                break

            if not raw_data:
                print(f"[SocketStreamer-{project_id}] No more data, container finished.")
                break

            buffer += raw_data
            while len(buffer) >= 8:
                header_bytes = buffer[:8]
                stream_type, size = struct.unpack('>BxxxL', header_bytes)
                if len(buffer) < 8 + size:
                    break
                payload_bytes = buffer[8 : 8 + size]
                buffer = buffer[8 + size:]
                stream_name = 'stdout' if stream_type == 1 else 'stderr'
                chunk_str = payload_bytes.decode('utf-8', 'replace')

                await send_ws_message(websocket, "code_output", {
                    "project_id": project_id, "stream": stream_name, "data": chunk_str
                })
    except Exception as e:
        if isinstance(e, asyncio.TimeoutError):
            raise
        print(f"[SocketStreamer-{project_id}] Unexpected error in stream: {e}")
        traceback.print_exc()
    finally:
        result = await asyncio.to_thread(container.wait, timeout=10)
        exit_code = result.get("StatusCode", 0)
        error_msg = result.get("Error")
        await send_ws_message(websocket, "code_finished", {
            "project_id": project_id, "exit_code": exit_code, "error": error_msg
        })
        try:
            socket.close()
        except Exception:
            pass

async def run_code_in_docker(
    websocket: WebSocket,
    client_id: str,
    project_id: str,
    project_path: str,
    run_command: str,
    lang_config: Dict[str, Any]
):
    local_docker_client = get_docker_client()
    if not local_docker_client:
        await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": "Docker service is unavailable."})
        return

    # --- BRANCH 1: Web Server Preview (Unchanged) ---
    if lang_config.get("is_preview_server"):
        # ... (This logic remains the same as before)
        port_match = re.search(r'\b(\d{4,5})\b', run_command)
        container_port = int(port_match.group(1)) if port_match else 8000
        host_port = _find_free_port()
        port_mapping = {f'{container_port}/tcp': host_port}
        try:
            container = await asyncio.to_thread(
                local_docker_client.containers.run,
                image=lang_config["image"],
                command=["sh", "-c", run_command],
                volumes={project_path: {'bind': '/app', 'mode': 'rw'}},
                working_dir='/app',
                ports=port_mapping,
                detach=True,
                remove=True,
                mem_limit=config.DOCKER_MEM_LIMIT
            )
            preview_url = f"http://127.0.0.1:{host_port}"
            async with state.running_previews_lock:
                state.running_previews[project_id] = { "container": container, "url": preview_url }
            await send_ws_message(websocket, "project_preview_ready", { "project_id": project_id, "url": preview_url })
        except Exception as e:
            await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": f"Failed to start preview: {e}"})
    
    # --- BRANCH 2: Standard Interactive Execution ---
    else:
        # --- START: New Matplotlib Plot Handling Logic ---
        is_plot_script = False
        original_run_command = run_command
        try:
            is_python_run = "python" in run_command and run_command.strip().endswith(".py")
            if is_python_run:
                script_name = run_command.strip().split(" ")[-1]
                script_path = Path(project_path) / script_name
                if script_path.exists():
                    script_content = script_path.read_text()
                    if "plt.show()" in script_content:
                        is_plot_script = True
                        print(f"[DockerPlot] Matplotlib plot detected in {script_name}. Using plot harness.")
                        harness_script_content = f"""
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt, mpld3, json, traceback
user_code = \"\"\"
{script_content}
\"\"\"
try:
    plt.figure()
    exec(user_code, globals())
    if plt.get_fignums():
        all_plots_data = []
        for i in plt.get_fignums():
            fig = plt.figure(i)
            if fig.axes:
                all_plots_data.append(mpld3.fig_to_dict(fig))
            plt.close(fig)
        if all_plots_data:
            with open('/app/plot_output.json', 'w') as f:
                json.dump(all_plots_data, f)
            print("Plot data successfully generated.")
except Exception:
    traceback.print_exc()
"""
                        harness_path = Path(project_path) / "harness.py"
                        harness_path.write_text(harness_script_content)
                        run_command = "python harness.py"
        except Exception as e:
            print(f"[DockerPlot] Error during plot detection: {e}")
        # --- END: New Matplotlib Plot Handling Logic ---

        container = None
        try:
            container = await asyncio.to_thread(
                local_docker_client.containers.run,
                image=lang_config["image"],
                command=["sh", "-c", run_command], # Uses harness command if it's a plot
                # ... (rest of the container run config is the same)
                volumes={project_path: {'bind': '/app', 'mode': 'rw'}},
                working_dir='/app', mem_limit=config.DOCKER_MEM_LIMIT,
                stdin_open=True, tty=False, detach=True, remove=False
            )
            
            params = {'stdin': 1, 'stdout': 1, 'stderr': 1, 'stream': 1}
            interactive_socket = await asyncio.to_thread(container.attach_socket, params=params)

            async with state.running_containers_lock:
                state.running_containers[project_id] = { "container": container, "client_id": client_id, "socket": interactive_socket }

            loop = asyncio.get_running_loop()
            await read_from_socket_and_stream(loop, websocket, project_id, container, interactive_socket, config.DOCKER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": "Execution timed out."})
        except Exception as e:
            await send_ws_message(websocket, "code_finished", {"project_id": project_id, "error": f"Execution failed: {e}"})
        finally:
            # --- START: New Plot Data Extraction Logic ---
            if is_plot_script:
                plot_output_path = Path(project_path) / "plot_output.json"
                if plot_output_path.exists():
                    try:
                        plot_data = json.loads(plot_output_path.read_text())
                        await send_ws_message(websocket, "plot_output", {
                            "project_id": project_id,
                            "plot_data": plot_data
                        })
                        plot_output_path.unlink() # Clean up the file
                    except Exception as e:
                        print(f"[DockerPlot] Error reading or sending plot data: {e}")
            # --- END: New Plot Data Extraction Logic ---
            await stop_container(project_id)

# --- Cleanup and Input Functions ---
async def send_input_to_container(project_id: str, user_input: str):
    async with state.running_containers_lock:
        container_info = state.running_containers.get(project_id)
        if container_info and (socket := container_info.get("socket")):
            try:
                await asyncio.to_thread(socket.sendall, user_input.encode('utf-8'))
            except Exception as e:
                print(f"Error sending input via socket: {e}")

async def stop_container(project_id: str):
    # This function now intelligently stops either a preview or a standard container
    async with state.running_previews_lock:
        preview_info = state.running_previews.pop(project_id, None)
    
    if preview_info and (container := preview_info.get("container")):
        try:
            await asyncio.to_thread(container.stop, timeout=5)
            print(f"[DockerPreview] Stopped preview container for {project_id}")
        except Exception as e:
            print(f"[DockerPreview] Error stopping preview container for {project_id}: {e}")
        return

    async with state.running_containers_lock:
        container_info = state.running_containers.pop(project_id, None)
    
    if container_info and (container := container_info.get("container")):
        try:
            if (socket := container_info.get("socket")):
                socket.close()
            await asyncio.to_thread(container.stop, timeout=5)
            await asyncio.to_thread(container.remove, force=True)
            print(f"[DockerExec] Stopped standard container for {project_id}")
        except Exception as e:
            print(f"[DockerExec] Error stopping standard container for {project_id}: {e}")

async def cleanup_client_containers(client_id: str):
    async with state.running_containers_lock:
        ids_for_client = [p_id for p_id, info in state.running_containers.items() if info.get("client_id") == client_id]
    
    if ids_for_client:
        await asyncio.gather(*[stop_container(p_id) for p_id in ids_for_client])