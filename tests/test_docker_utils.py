import asyncio
import os
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock
from fastapi.websockets import WebSocketState

from app import docker_utils, config, state

@pytest.fixture(scope="module", autouse=True)
def manage_docker_worker():
    docker_utils.start_docker_worker()
    yield
    docker_utils.shutdown_docker_worker()

class MockWebSocket:
    def __init__(self):
        self.sent_messages = []
        self.scope = {"client_id": "pytest_client"}
        self.client_state = WebSocketState.CONNECTED
    
    async def send_json(self, message):
        self.sent_messages.append(message)

# --- Test Case Data ---

# 1. Fast, non-interactive "Hello World"
helloworld_py_content = 'print("Hello, World!")'
helloworld_project_data = {
    "files": [
        {"path": "main.py", "content": helloworld_py_content},
        {"path": "run.sh", "content": "python3 main.py"}
    ]
}

# 2. Interactive Python with streaming
interactive_py_content = (
    'import time\nimport sys\n\n'
    'print("Container: Starting python countdown...", flush=True)\n'
    'for i in range(1, 6):\n'
    '    print(f"Container: ...{i}", flush=True)\n'
    '    time.sleep(0.2)\n'
    'print("Container: What is your name? ", flush=True)\n'
    'name = sys.stdin.readline().strip()\n'
    'print(f"Container: Hello, {name}!", flush=True)\n'
)
interactive_py_project_data = {
    "files": [
        {"path": "main.py", "content": interactive_py_content},
        {"path": "run.sh", "content": "python3 main.py"}
    ]
}

# 3. Interactive C++ with streaming
interactive_cpp_content = (
    '#include <iostream>\n#include <string>\n#include <thread>\n#include <chrono>\n\n'
    'int main() { \n'
    '    std::cout << "Container: Starting C++ countdown..." << std::flush;\n'
    '    for (int i = 1; i <= 5; ++i) {\n'
    '        std::cout << "\\nContainer: ..." << i << std::flush;\n'
    '        std::this_thread::sleep_for(std::chrono::milliseconds(200));\n'
    '    }\n'
    '    std::cout << "\\nContainer: What is your name? " << std::flush; \n'
    '    std::string name; \n'
    '    std::getline(std::cin, name); \n'
    '    std::cout << "\\nContainer: Hello, " << name << "!" << std::endl; \n'
    '    return 0; \n'
    '}'
)
interactive_cpp_run_sh = (
    '#!/bin/sh\n'
    'g++ -std=c++11 main.cpp -o main_app\n'
    'if [ $? -ne 0 ]; then\n'
    '  echo "C++ compilation failed!"\n'
    '  exit 1\n'
    'fi\n'
    './main_app\n'
)
interactive_cpp_project_data = {
    "files": [
        {"path": "main.cpp", "content": interactive_cpp_content},
        {"path": "run.sh", "content": interactive_cpp_run_sh}
    ]
}

@pytest.mark.asyncio
async def test_fast_execution():
    """
    Tests a simple, non-interactive script that finishes very quickly
    to ensure there are no race conditions or hangs.
    """
    lang_config = config.SUPPORTED_LANGUAGES.get("python")
    project_data = helloworld_project_data
    language = "python-fast"
    
    project_path = None
    mock_ws = MockWebSocket()
    loop = asyncio.get_running_loop()

    try:
        SHARED_PROJECTS_DIR_IN_CONTAINER = "/projects"
        os.makedirs(SHARED_PROJECTS_DIR_IN_CONTAINER, exist_ok=True)
        project_path = tempfile.mkdtemp(dir=SHARED_PROJECTS_DIR_IN_CONTAINER)

        for file_info in project_data["files"]:
            file_path = os.path.join(project_path, file_info["path"])
            with open(file_path, "w", encoding="utf-8", newline='\n') as f:
                f.write(file_info["content"])
        os.chmod(os.path.join(project_path, "run.sh"), 0o755)

        project_id = f"fast_exec_test_{language}"
        
        exit_code, full_output, error_message = await docker_utils.run_code_in_docker(
            websocket=mock_ws,
            client_id="test_client_id",
            project_id=project_id,
            project_data=project_data,
            project_path=project_path,
            run_command="sh run.sh",
            lang_config=lang_config,
            loop=loop
        )
        
        assert error_message is None, f"Execution returned an error: {error_message}"
        assert exit_code == 0, f"Expected exit code 0, but got {exit_code}. Logs:\n{full_output}"
        assert "Hello, World!" in full_output, f"Expected 'Hello, World!' not found. Logs:\n{full_output}"

    finally:
        await docker_utils.stop_container(project_id)
        if project_path and os.path.exists(project_path):
            shutil.rmtree(project_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "language, project_data",
    [
        ("python", interactive_py_project_data),
        ("cpp", interactive_cpp_project_data),
    ]
)
async def test_interactive_code_execution(language, project_data):
    lang_config = config.SUPPORTED_LANGUAGES.get(language)
    assert lang_config is not None, f"Language config for '{language}' not found."
    
    project_path = None
    mock_ws = MockWebSocket()
    loop = asyncio.get_running_loop()

    try:
        SHARED_PROJECTS_DIR_IN_CONTAINER = "/projects"
        os.makedirs(SHARED_PROJECTS_DIR_IN_CONTAINER, exist_ok=True)
        project_path = tempfile.mkdtemp(dir=SHARED_PROJECTS_DIR_IN_CONTAINER)

        for file_info in project_data["files"]:
            file_path = os.path.join(project_path, file_info["path"])
            with open(file_path, "w", encoding="utf-8", newline='\n') as f:
                f.write(file_info["content"])
        os.chmod(os.path.join(project_path, "run.sh"), 0o755)

        project_id = f"interactive_test_{language}"
        
        execution_finished = asyncio.Event()
        exit_code = -1
        full_output = ""
        error_message = ""

        async def run_and_monitor():
            nonlocal exit_code, full_output, error_message
            exit_code, full_output, error_message = await docker_utils.run_code_in_docker(
                websocket=mock_ws,
                client_id="test_client_id",
                project_id=project_id,
                project_data=project_data,
                project_path=project_path,
                run_command="sh run.sh",
                lang_config=lang_config,
                loop=loop
            )
            execution_finished.set()

        exec_task = asyncio.create_task(run_and_monitor())
        
        async def interact():
            while not exec_task.done():
                for msg in mock_ws.sent_messages:
                    if "What is your name?" in msg.get("payload", {}).get("data", ""):
                        await docker_utils.send_input_to_container(project_id, "Tester")
                        return
                await asyncio.sleep(0.1)
        
        interact_task = asyncio.create_task(interact())

        await asyncio.wait_for(execution_finished.wait(), timeout=20)
        interact_task.cancel()

        assert error_message is None, f"Execution returned an error: {error_message}"
        assert exit_code == 0, f"Expected exit code 0, but got {exit_code}. Logs:\n{full_output}"
        
        assert "Hello, Tester!" in full_output, f"Expected interactive output not found. Logs:\n{full_output}"
        assert "Container: ...5" in full_output, f"Expected streaming output not found. Logs:\n{full_output}"

    finally:
        await docker_utils.stop_container(project_id)
        if project_path and os.path.exists(project_path):
            shutil.rmtree(project_path)

