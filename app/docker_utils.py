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
import json # Ensure json is imported
import re   # --- NEW: Import re module ---
import ast
import os
from typing import List

STD_LIB_MODULES = {
    'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio', 'asyncore', 'atexit',
    'audioop', 'base64', 'bdb', 'binascii', 'binhex', 'bisect', 'builtins', 'bz2',
    'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd', 'code', 'codecs', 'codeop',
    'collections', 'colorsys', 'compileall', 'concurrent', 'configparser', 'contextlib',
    'contextvars', 'copy', 'copyreg', 'crypt', 'csv', 'ctypes', 'curses', 'dataclasses',
    'datetime', 'dbm', 'decimal', 'difflib', 'dis', 'distutils', 'doctest', 'dummy_threading',
    'email', 'encodings', 'ensurepip', 'enum', 'errno', 'faulthandler', 'fcntl', 'filecmp',
    'fileinput', 'fnmatch', 'fractions', 'ftplib', 'functools', 'gc', 'getopt', 'getpass',
    'gettext', 'glob', 'grp', 'gzip', 'hashlib', 'heapq', 'hmac', 'html', 'http', 'imaplib',
    'imghdr', 'imp', 'importlib', 'inspect', 'io', 'ipaddress', 'itertools', 'json',
    'keyword', 'lib2to3', 'linecache', 'locale', 'logging', 'lzma', 'mailbox', 'mailcap',
    'marshal', 'math', 'mimetypes', 'mmap', 'modulefinder', 'multiprocessing', 'netrc',
    'nis', 'nntplib', 'numbers', 'operator', 'optparse', 'os', 'ossaudiodev', 'parser',
    'pathlib', 'pdb', 'pickle', 'pickletools', 'pipes', 'pkgutil', 'platform', 'plistlib',
    'poplib', 'posix', 'pprint', 'profile', 'pstats', 'pty', 'pwd', 'py_compile', 'pyclbr',
    'pydoc', 'queue', 'quopri', 'random', 're', 'readline', 'reprlib', 'resource', 'rlcompleter',
    'runpy', 'sched', 'secrets', 'select', 'selectors', 'shelve', 'shlex', 'shutil',
    'signal', 'site', 'smtpd', 'smtplib', 'sndhdr', 'socket', 'socketserver', 'spwd',
    'sqlite3', 'ssl', 'stat', 'statistics', 'string', 'stringprep', 'struct', 'subprocess',
    'sunau', 'symbol', 'symtable', 'sys', 'sysconfig', 'syslog', 'tabnanny', 'tarfile',
    'telnetlib', 'tempfile', 'termios', 'textwrap', 'threading', 'time', 'timeit', 'tkinter',
    'token', 'tokenize', 'trace', 'traceback', 'tracemalloc', 'tty', 'turtle', 'turtledemo',
    'types', 'typing', 'unicodedata', 'unittest', 'urllib', 'uu', 'uuid', 'venv', 'warnings',
    'wave', 'weakref', 'webbrowser', 'wsgiref', 'xdrlib', 'xml', 'xmlrpc', 'zipapp',
    'zipfile', 'zipimport', 'zlib'
}

# Import pywintypes if on Windows, otherwise ignore
try:
    import pywintypes
    PIPE_ENDED_ERROR = pywintypes.error
except ImportError:
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

def find_python_imports(code: str) -> List[str]:
    """
    Analyzes Python code using AST to find top-level modules that need to be installed.
    """
    try:
        tree = ast.parse(code)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])
        
        # Filter out standard library modules from the found imports
        non_std_lib_imports = {imp for imp in imports if imp not in STD_LIB_MODULES}
        
        print(f"[find_python_imports] Found potential packages to install: {non_std_lib_imports}")
        return sorted(list(non_std_lib_imports))
    except SyntaxError as e:
        print(f"[find_python_imports] Syntax error analyzing code: {e}")
        return []
    except Exception as e:
        print(f"[find_python_imports] Error analyzing code with AST: {e}")
        return []

async def read_from_socket_and_stream(
    loop: asyncio.AbstractEventLoop,
    websocket: WebSocket,
    code_block_id: str,
    container: Container,
    socket: socket.socket
):
    # This function remains unchanged...
    import struct
    buffer = b''
    try:
        while True:
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
                    "code_block_id": code_block_id, "stream": stream_name, "data": chunk_str
                })

                if stream_name == 'stdout':
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
    
    final_command = lang_config["command"]
    volumes_to_mount = {str(tmp_path): {'bind': '/app', 'mode': 'rw'}}
    code_to_run = code

    if language.lower() == 'python':
        code = re.sub(r'^\s*plt\.show\(\s*\)\s*$', '', code, flags=re.MULTILINE)
        
        packages_to_install = find_python_imports(code)
        volumes_to_mount['tesseracs-uv-cache'] = {'bind': '/root/.cache/uv', 'mode': 'rw'}
        
        # --- MODIFIED: Harness now uses the correct mpld3.fig_to_dict() function ---
        python_harness_script = f"""
import sys
import json
import traceback
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mpld3

user_code = '''
{code}
'''

try:
    exec(user_code, globals())

finally:
    fignums = plt.get_fignums()
    if fignums:
        print(f"[Harness] {{len(fignums)}} Matplotlib plots detected. Capturing all.", file=sys.stderr)
        all_plots_data = []
        for i in fignums:
            fig = plt.figure(i)
            try:
                # 1. Convert the figure to a dictionary using the correct function
                plot_dict = mpld3.fig_to_dict(fig)
                all_plots_data.append(plot_dict)
            except Exception as e:
                print(f"[Harness] Error capturing plot figure {{i}}: {{e}}", file=sys.stderr)
            finally:
                plt.close(fig)
        
        if all_plots_data:
            # 2. Save the list of dictionaries to the file using the json library
            with open('/app/plot_output.json', 'w') as f:
                json.dump(all_plots_data, f)
"""
        code_to_run = python_harness_script
        # --- End of modification ---
        # --- End of modification ---
        # --- End of modification ---

        install_command_parts = []
        if packages_to_install:
            install_command_parts.append(f"uv pip install --system --no-cache-dir {' '.join(packages_to_install)}")
        
        run_command = "stdbuf -o0 python -u /app/script.py"
        install_command_parts.append(run_command)
        
        final_command = ["sh", "-c", " && ".join(install_command_parts)]
        print(f"[DockerRun-{code_block_id}] Modified command for Python: {final_command}")

    try:
        (tmp_path / lang_config["filename"]).write_text(code_to_run, encoding="utf-8")
        
        is_interactive = lang_config.get("interactive", True)
        
        container = local_docker_client.containers.run(
            image=lang_config["image"],
            command=final_command,
            volumes=volumes_to_mount,
            working_dir='/app',
            mem_limit=config.DOCKER_MEM_LIMIT,
            stdin_open=True,
            tty=False,
            detach=True,
            remove=False
        )

        if is_interactive:
            params = {'stdin': 1, 'stdout': 1, 'stderr': 1, 'stream': 1}
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
            container.wait(timeout=config.DOCKER_TIMEOUT_SECONDS)

    except asyncio.TimeoutError:
        print(f"[DockerRun-{code_block_id}] Session timed out after {config.DOCKER_TIMEOUT_SECONDS}s.")
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": 137, "error": "Execution timed out."})
    except Exception as e:
        error_payload = f"Server Execution Error: {e}"
        print(f"[DockerRun-{code_block_id}] CRITICAL ERROR: {error_payload}")
        traceback.print_exc()
        await send_ws_message(websocket, "code_finished", {"code_block_id": code_block_id, "exit_code": -1, "error": error_payload})
    finally:
        plot_output_path = tmp_path / "plot_output.json"
        if plot_output_path.exists():
            print(f"[DockerRun-{code_block_id}] Found plot_output.json. Sending to client.")
            try:
                with open(plot_output_path, 'r') as f:
                    plot_data = json.load(f) # This will now be a list
                await send_ws_message(websocket, "plot_output", {
                    "code_block_id": code_block_id,
                    "plot_data": plot_data
                })
            except Exception as e:
                print(f"[DockerRun-{code_block_id}] Error reading or sending plot data: {e}")
        
        print(f"[DockerRun-{code_block_id}] === CLEANUP PHASE ===")
        await stop_docker_container(code_block_id)
        if tmpdir_obj:
            try:
                tmpdir_obj.cleanup()
            except Exception as e:
                print(f"[DockerRun-{code_block_id}] Error cleaning temp directory: {e}")
        
        print(f"[DockerRun-{code_block_id}] === DOCKER EXECUTION COMPLETE ===")

async def send_input_to_container(code_block_id: str, user_input: str):
    # This function remains unchanged...
    async with state.running_containers_lock:
        container_info = state.running_containers.get(code_block_id)
        if container_info and (socket := container_info.get("socket")):
            try:
                await asyncio.to_thread(socket.sendall, user_input.encode('utf-8'))
                print(f"Sent input to {code_block_id}: {user_input.strip()}")
            except Exception as e:
                print(f"Error sending input via socket: {e}")
        else:
            print(f"Send input failed: No active container or socket for {code_block_id}")

async def stop_docker_container(code_block_id: str):
    # This function remains unchanged...
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
            await asyncio.to_thread(container.reload)
            if container.status == 'running':
                await asyncio.to_thread(container.kill)
                print(f"Container for {code_block_id} killed.")
            else:
                print(f"Container for {code_block_id} already stopped (status: {container.status}).")
            
            await asyncio.to_thread(container.remove, force=True)
            print(f"Container for {code_block_id} removed.")
        except Exception as e:
            print(f"Error stopping/removing container for {code_block_id}: {e}")

async def cleanup_client_containers(client_id: str):
    # This function remains unchanged...
    async with state.running_containers_lock:
        ids_for_client = [cb_id for cb_id, info in state.running_containers.items() if info.get("client_id") == client_id]
    
    if ids_for_client:
        await asyncio.gather(*[stop_docker_container(cb_id) for cb_id in ids_for_client], return_exceptions=True)