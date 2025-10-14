import asyncio
import os
import shutil
import tempfile
import traceback
import docker
import socket
import struct
import time
import select
from multiprocessing import Process, Pipe
from multiprocessing.connection import Connection
from typing import Dict, Any

STREAM_END_SIGNAL = "__DOCKER_STREAM_END__"
ERROR_PREFIX = "DOCKER_ERROR::"
try:
    import pywintypes
    PIPE_ENDED_ERROR = pywintypes.error
except ImportError:
    class DummyPipeEndedError(Exception): pass
    PIPE_ENDED_ERROR = DummyPipeEndedError

# ==============================================================================
# WORKER PROCESS LOGIC
# ==============================================================================

def worker_get_host_path(docker_client, container_path: str) -> str:
    try:
        container_id = socket.gethostname()
        container = docker_client.containers.get(container_id)
        mounts = container.attrs['Mounts']
        for mount in sorted(mounts, key=lambda m: len(m['Destination']), reverse=True):
            if container_path.startswith(mount['Destination']):
                relative_path = os.path.relpath(container_path, mount['Destination'])
                return os.path.join(mount['Source'], relative_path)
    except Exception as e:
        print(f"[Worker] Path translation error: {e}")
    return container_path

def worker_process_job(conn: Connection, job: Dict[str, Any]):
    docker_client = docker.from_env()
    container = None
    socket_obj = None
    try:
        project_path = job["project_path"]
        lang_config = job["lang_config"]
        host_project_path = worker_get_host_path(docker_client, project_path)
        
        container = docker_client.containers.create(
            image=lang_config["image"],
            command=["sh", "run.sh"],
            volumes={host_project_path: {'bind': '/app', 'mode': 'rw'}},
            working_dir='/app',
            stdin_open=True, tty=False, detach=True,
        )

        socket_obj = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        container.start()

        conn.send({"type": "container_started", "container_id": container.id})

        raw_sock = socket_obj._sock if hasattr(socket_obj, '_sock') else socket_obj
        raw_sock.setblocking(False)
        
        buffer = b''
        read_sockets = [raw_sock, conn]

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
            
            readable, _, _ = select.select(read_sockets, [], [], 0.1)

            for s in readable:
                if s is conn:
                    try:
                        msg = conn.recv()
                        if msg.get("type") == "input":
                            user_input = msg.get("data", "")
                            if not user_input.endswith('\n'): user_input += '\n'
                            if raw_sock in read_sockets:
                                raw_sock.sendall(user_input.encode('utf-8'))
                    except (EOFError, BrokenPipeError):
                        read_sockets.remove(conn)
                    continue

                if s is raw_sock:
                    try:
                        raw_data = raw_sock.recv(4096)
                        if not raw_data:
                            read_sockets.remove(raw_sock)
                        else:
                            buffer += raw_data
                    except (BlockingIOError, InterruptedError):
                        continue
                    except (ConnectionResetError, BrokenPipeError, PIPE_ENDED_ERROR):
                        read_sockets.remove(raw_sock)

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
        if socket_obj: socket_obj.close()
        if container:
            try: container.remove(force=True)
            except docker.errors.NotFound: pass
        conn.send(STREAM_END_SIGNAL)

def start_worker(conn: Connection):
    while True:
        try:
            job = conn.recv()
            if job == "EXIT": break
            if job.get("type") == "start":
                worker_process_job(conn, job)
        except (EOFError, BrokenPipeError):
            break
        except Exception:
            try: conn.send(f"{ERROR_PREFIX}{traceback.format_exc()}")
            except Exception: pass
    print("[Worker Process]: Exiting.")

# ==============================================================================
# MAIN ASYNC SCRIPT LOGIC
# ==============================================================================
async def run_test(parent_conn, language, project_data, lang_config):
    project_path = None
    print(f"\n--- Starting Test for [{language.upper()}] ---")
    
    try:
        SHARED_PROJECTS_DIR = "/projects"
        os.makedirs(SHARED_PROJECTS_DIR, exist_ok=True)
        project_path = tempfile.mkdtemp(dir=SHARED_PROJECTS_DIR)

        for file_info in project_data["files"]:
            with open(os.path.join(project_path, file_info["path"]), "w", newline='\n') as f:
                f.write(file_info["content"])
        os.chmod(os.path.join(project_path, "run.sh"), 0o755)
        print(f"✅ Project created at: {project_path}")

        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        
        def pipe_data_received():
            try:
                queue.put_nowait(parent_conn.recv())
            except Exception:
                queue.put_nowait(STREAM_END_SIGNAL)
        
        loop.add_reader(parent_conn.fileno(), pipe_data_received)

        job = {"type": "start", "project_path": project_path, "lang_config": lang_config}
        print("\nMain - 🏃‍♂️ Sending job to worker process...")
        await loop.run_in_executor(None, parent_conn.send, job)

        full_output = []
        exit_code = -1
        input_sent = False
        while True:
            msg = await asyncio.wait_for(queue.get(), timeout=20)
            
            if msg == STREAM_END_SIGNAL:
                print("Main - ✅ Received stream end signal.")
                break
            if isinstance(msg, str) and msg.startswith(ERROR_PREFIX):
                raise Exception(msg)

            if msg.get("type") == "chunk":
                chunk_data = msg.get("data", "")
                print(f"Main - Received chunk: {chunk_data.strip()}")
                full_output.append(chunk_data)
                if "What is your name?" in chunk_data and not input_sent:
                    print("Main - >>> PROMPT DETECTED! Sending input... <<<")
                    await loop.run_in_executor(None, parent_conn.send, {"type": "input", "data": "Tester"})
                    input_sent = True
            elif msg.get("type") == "exit_code":
                exit_code = msg.get("exit_code")
                print(f"Main - Received exit code: {exit_code}")

        print("\n--- RESULTS ---")
        print(f"📦 Exit Code: {exit_code}")
        final_logs = "".join(full_output)
        print(f"📜 Logs:\n{final_logs}")
        assert exit_code == 0
        assert "Hello, Tester!" in final_logs
        assert "Container: ...5" in final_logs
        print("\n✅ Assertions Passed!")

    finally:
        loop.remove_reader(parent_conn.fileno())
        if project_path and os.path.exists(project_path):
            shutil.rmtree(project_path)
            print("✅ Temporary project directory cleaned up.")

async def main():
    python_project_data = {
        "files": [
            {
                "path": "main.py",
                "content": (
                    'import time\nimport sys\n\n'
                    'print("Container: Starting python countdown...", flush=True)\n'
                    'for i in range(1, 6):\n'
                    '    print(f"Container: ...{i}", flush=True)\n'
                    '    time.sleep(1)\n'
                    'print("Container: What is your name? ", flush=True)\n'
                    'name = sys.stdin.readline().strip()\n'
                    'print(f"Container: Hello, {name}!", flush=True)\n'
                )
            },
            {"path": "run.sh", "content": "python3 main.py"}
        ]
    }
    cpp_project_data = {
        "files": [
            {
                "path": "main.cpp",
                "content": (
                    '#include <iostream>\n#include <string>\n#include <thread>\n#include <chrono>\n\n'
                    'int main() { \n'
                    '    std::cout << "Container: Starting C++ countdown..." << std::flush;\n'
                    '    for (int i = 1; i <= 5; ++i) {\n'
                    '        std::cout << "\\nContainer: ..." << i << std::flush;\n'
                    '        std::this_thread::sleep_for(std::chrono::seconds(1));\n'
                    '    }\n'
                    '    std::cout << "\\nContainer: What is your name? " << std::flush; \n'
                    '    std::string name; \n'
                    '    std::getline(std::cin, name); \n'
                    '    std::cout << "\\nContainer: Hello, " << name << "!" << std::endl; \n'
                    '    return 0; \n'
                    '}'
                )
            },
            {"path": "run.sh", "content": "g++ -std=c++11 main.cpp -o app && ./app"}
        ]
    }
    
    parent_conn, child_conn = Pipe()
    worker = Process(target=start_worker, args=(child_conn,))
    worker.start()
    child_conn.close()

    try:
        await run_test(parent_conn, "python", python_project_data, {"image": "tesseracs-python"})
        await run_test(parent_conn, "cpp", cpp_project_data, {"image": "tesseracs-gcc"})
    except Exception:
        print("\n--- ❌ AN ERROR OCCURRED ---")
        traceback.print_exc()
    finally:
        if parent_conn: parent_conn.send("EXIT")
        if worker and worker.is_alive(): worker.join(timeout=5)
        if worker and worker.is_alive(): worker.terminate()
        print("\n--- Test Finished ---")

if __name__ == "__main__":
    asyncio.run(main())

