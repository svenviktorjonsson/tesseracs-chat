import os
import traceback
import docker
import socket
import struct
import select
from multiprocessing.connection import Connection
from typing import Dict, Any
import app.config as config

STREAM_END_SIGNAL = "__DOCKER_STREAM_END__"
ERROR_PREFIX = "DOCKER_ERROR::"
docker_client = None

try:
    import pywintypes
    PIPE_ENDED_ERROR = pywintypes.error
except ImportError:
    class DummyPipeEndedError(Exception): pass
    PIPE_ENDED_ERROR = DummyPipeEndedError

def get_host_path_from_container_path(container_path: str) -> str:
    try:
        container_id = socket.gethostname()
        container = docker_client.containers.get(container_id)
        mounts = container.attrs['Mounts']
        for mount in sorted(mounts, key=lambda m: len(m['Destination']), reverse=True):
            container_mount_point = mount['Destination']
            host_mount_point = mount['Source']
            if container_path.startswith(container_mount_point):
                relative_path = os.path.relpath(container_path, container_mount_point)
                return os.path.join(host_mount_point, relative_path)
    except Exception as e:
        print(f"[Worker] Path translation error: {e}")
    return container_path

def process_job(conn: Connection, job: Dict[str, Any]):
    container = None
    socket_obj = None
    try:
        project_id = job["project_id"]
        project_path = job["project_path"]
        lang_config = job["lang_config"]
        host_project_path = get_host_path_from_container_path(project_path)
        
        container = docker_client.containers.create(
            image=lang_config["image"],
            command=["sh", "run.sh"],
            volumes={host_project_path: {'bind': '/app', 'mode': 'rw'}},
            working_dir='/app',
            stdin_open=True, tty=False, detach=True,
            mem_limit=config.DOCKER_MEM_LIMIT,
            labels={"managed-by": "tesseracs-chat"}
        )

        socket_obj = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        container.start()

        conn.send({"type": "container_started", "container_id": container.id, "project_id": project_id})

        raw_sock = socket_obj._sock if hasattr(socket_obj, '_sock') else socket_obj
        raw_sock.setblocking(False)
        
        buffer = b''
        read_sockets = [raw_sock, conn]
        waiting_signal_sent = False

        while read_sockets:
            is_running = True
            try:
                container.reload()
                if container.status != 'running':
                    is_running = False
            except docker.errors.NotFound:
                is_running = False

            if not is_running and not buffer and not (conn in read_sockets and conn.poll()):
                break
            
            readable, _, _ = select.select(read_sockets, [], [], 1.0)

            if not readable and is_running:
                if not waiting_signal_sent:
                    conn.send({"type": "waiting_for_input", "project_id": project_id})
                    waiting_signal_sent = True
                continue
            
            for s in readable:
                if s is conn:
                    try:
                        msg = conn.recv()
                        if msg.get("type") == "input":
                            waiting_signal_sent = False
                            user_input = msg.get("data", "")
                            if not user_input.endswith('\n'): user_input += '\n'
                            if raw_sock in read_sockets:
                                raw_sock.sendall(user_input.encode('utf-8'))
                    except (EOFError, BrokenPipeError):
                        if conn in read_sockets: read_sockets.remove(conn)
                    # --- THE FIX: The problematic 'continue' statement is removed from here ---

                if s is raw_sock:
                    try:
                        raw_data = s.recv(4096)
                        if not raw_data:
                            if s in read_sockets: read_sockets.remove(s)
                        else:
                            buffer += raw_data
                    except (BlockingIOError, InterruptedError):
                        continue
                    except (ConnectionResetError, BrokenPipeError, PIPE_ENDED_ERROR):
                        if s in read_sockets: read_sockets.remove(s)

            while len(buffer) >= 8:
                header = buffer[:8]
                stream_type, size = struct.unpack('>BxxxL', header)
                if len(buffer) < 8 + size: break
                payload = buffer[8 : 8 + size].decode('utf-8', 'replace')
                conn.send({"type": "chunk", "stream": "stdout" if stream_type == 1 else "stderr", "data": payload})
                buffer = buffer[8 + size:]
        
        result = container.wait()
        conn.send({"type": "exit_code", "exit_code": result.get("StatusCode", -1)})

    except Exception:
        conn.send(f"{ERROR_PREFIX}{traceback.format_exc()}")
    finally:
        if socket_obj: 
            socket_obj.close()
        
        if container:
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        
        conn.send(STREAM_END_SIGNAL)

def start_worker(conn: Connection):
    global docker_client
    docker_client = docker.from_env()
    
    while True:
        try:
            job = conn.recv()
            if job == "EXIT": break
            if job.get("type") == "start":
                process_job(conn, job)
        except (EOFError, BrokenPipeError):
            break
        except Exception:
            try: conn.send(f"{ERROR_PREFIX}{traceback.format_exc()}")
            except Exception: pass
    print("[Worker Process]: Exiting.")

