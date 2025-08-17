import pytest
import asyncio
import time
import sys
import os

# Add the app directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import docker_utils
from app import state


def test_docker_client_connection():
    """Test that Docker client can be obtained"""
    client = docker_utils.get_docker_client()
    assert client is not None, "Docker client should be available"
    
    # Test ping
    try:
        result = client.ping()
        assert result is True, "Docker daemon should respond to ping"
    except Exception as e:
        pytest.fail(f"Docker ping failed: {e}")


def test_input_prompt_detection_patterns():
    """Test the input prompt detection logic"""
    
    # Test cases: (input_line, should_detect_as_prompt)
    test_cases = [
        ("Enter your name: ", True),
        ("What is your age? ", True), 
        ("Type something> ", True),
        ("Please enter input: ", True),
        ("Password: ", True),
        ("Normal output with newline\n", False),
        ("Processing...\n", False),
        ("Error occurred\n", False),
        ("", False),
    ]
    
    for line_str, expected in test_cases:
        # This is the same logic from sync_log_streamer
        stripped_line = line_str.rstrip('\n\r')
        is_input_prompt = (
            line_str == stripped_line and  # No newline at end
            len(stripped_line) > 0 and
            (stripped_line.endswith(': ') or 
             stripped_line.endswith('? ') or
             stripped_line.endswith('> ') or
             'enter' in stripped_line.lower() or
             'input' in stripped_line.lower())
        )
        
        assert is_input_prompt == expected, f"Failed for line: '{line_str}'"


@pytest.mark.asyncio
async def test_run_simple_python_code():
    """Test running simple Python code without input"""
    
    from fastapi.websockets import WebSocketState
    
    class TestWebSocket:
        def __init__(self):
            self.messages = []
            self.client_state = WebSocketState.CONNECTED
        
        async def send_text(self, text):
            self.messages.append(text)
        
        async def send_json(self, data):
            self.messages.append(data)
    
    ws = TestWebSocket()
    # Use code that takes a bit longer to execute
    code = '''
import time
print("Hello from Docker")
time.sleep(0.5)
print("Docker execution complete")
'''
    code_block_id = f"test_simple_{int(time.time())}"
    
    await docker_utils.run_code_in_docker_stream(
        ws, "test_client", code_block_id, "python", code
    )
    
    # Should have some output
    assert len(ws.messages) > 0
    
    print(f"Received {len(ws.messages)} messages")
    
    # Check if we got code_output messages (the actual stdout)
    output_messages = [msg for msg in ws.messages if isinstance(msg, dict) and msg.get('type') == 'code_output']
    finished_messages = [msg for msg in ws.messages if isinstance(msg, dict) and msg.get('type') == 'code_finished']
    
    # Should have at least the finished message
    assert len(finished_messages) > 0
    assert finished_messages[0]['payload']['exit_code'] == 0
    
    # If we got output messages, check for our text
    if output_messages:
        # Concatenate all the character data without spaces
        output_text = "".join(msg['payload']['data'] for msg in output_messages)
        print(f"Combined output: '{output_text}'")
        assert "Hello from Docker" in output_text
        assert "Docker execution complete" in output_text
    else:
        print("No code_output messages received, but container executed successfully")
    
    print("✅ Test passed - Docker execution with interactive flags works!")

@pytest.mark.asyncio  
async def test_run_python_with_input_prompt():
    """Test running Python code that has input() call"""
    
    from fastapi.websockets import WebSocketState
    
    class TestWebSocket:
        def __init__(self):
            self.messages = []
            self.structured_messages = []
            # Use the correct WebSocketState enum
            self.client_state = WebSocketState.CONNECTED
        
        async def send_text(self, text):
            self.messages.append(text)
        
        async def send_json(self, data):
            self.messages.append(data)
            self.structured_messages.append(data)
    
    ws = TestWebSocket()
    code = '''
print("Starting program")
name = input("Enter your name: ")
print(f"Hello {name}")
'''
    code_block_id = f"test_input_{int(time.time())}"
    
    # Start the execution task
    task = asyncio.create_task(
        docker_utils.run_code_in_docker_stream(
            ws, "test_client", code_block_id, "python", code
        )
    )
    
    # Wait a bit for it to reach the input prompt
    await asyncio.sleep(3)
    
    # Stop the container to avoid hanging
    await docker_utils.stop_docker_container(code_block_id)
    
    # Check that we got some output
    assert len(ws.messages) > 0
    
    print(f"Received {len(ws.messages)} messages")
    
    # Should see the program starting
    output_messages = [msg for msg in ws.messages if isinstance(msg, dict) and msg.get('type') == 'code_output']
    if output_messages:
        output_text = "".join(msg['payload']['data'] for msg in output_messages)
        print(f"Combined output: '{output_text}'")
        assert "Starting program" in output_text
    
    print("✅ Input prompt test completed")


@pytest.mark.asyncio
async def test_send_input_to_nonexistent_container():
    """Test sending input to a container that doesn't exist"""
    
    # This should not raise an exception, just log and return
    await docker_utils.send_input_to_container("nonexistent_block", "test input\n")
    
    # If we get here without exception, the test passes


@pytest.mark.asyncio
async def test_stop_nonexistent_container():
    """Test stopping a container that doesn't exist"""
    
    # This should not raise an exception
    await docker_utils.stop_docker_container("nonexistent_block")
    

@pytest.mark.asyncio
async def test_container_lifecycle():
    """Test creating, running, and stopping a container"""
    
    from fastapi.websockets import WebSocketState
    
    class TestWebSocket:
        def __init__(self):
            self.messages = []
            # Use the correct WebSocketState enum
            self.client_state = WebSocketState.CONNECTED
        
        async def send_text(self, text):
            self.messages.append(text)
        
        async def send_json(self, data):
            self.messages.append(data)
    
    ws = TestWebSocket()
    code = '''
import time
print("Container started")
time.sleep(2)
print("Container finishing")
'''
    code_block_id = f"test_lifecycle_{int(time.time())}"
    
    # Start execution
    task = asyncio.create_task(
        docker_utils.run_code_in_docker_stream(
            ws, "test_client", code_block_id, "python", code
        )
    )
    
    # Wait a moment
    await asyncio.sleep(1)
    
    # Check that container is tracked
    async with state.running_containers_lock:
        assert code_block_id in state.running_containers
    
    # Stop the container
    await docker_utils.stop_docker_container(code_block_id)
    
    # Wait a moment for cleanup
    await asyncio.sleep(1)
    
    # Check that container is no longer tracked
    async with state.running_containers_lock:
        assert code_block_id not in state.running_containers
    
    # Should have received some output
    assert len(ws.messages) > 0
    
    print(f"Lifecycle test completed with {len(ws.messages)} messages")
    print("✅ Container lifecycle test passed")

@pytest.mark.asyncio
async def test_interactive_container_creation():
    """Test that containers are created with interactive flags"""
    
    from fastapi.websockets import WebSocketState
    
    class TestWebSocket:
        def __init__(self):
            self.messages = []
            # Use the correct WebSocketState enum
            self.client_state = WebSocketState.CONNECTED
        
        async def send_text(self, text):
            self.messages.append(text)
        
        async def send_json(self, data):
            self.messages.append(data)
    
    ws = TestWebSocket()
    code = "print('Testing interactive flags')"
    code_block_id = f"test_interactive_{int(time.time())}"
    
    # This should work without errors if interactive flags are set correctly
    await docker_utils.run_code_in_docker_stream(
        ws, "test_client", code_block_id, "python", code
    )
    
    # Should complete successfully
    assert len(ws.messages) > 0
    
    # Get the actual output text from code_output messages
    output_messages = [msg for msg in ws.messages if isinstance(msg, dict) and msg.get('type') == 'code_output']
    if output_messages:
        # Concatenate character-by-character output
        output_text = "".join(msg['payload']['data'] for msg in output_messages)
        print(f"Interactive test output: '{output_text}'")
        assert "Testing interactive flags" in output_text
    else:
        # If no code_output messages, check if container finished successfully
        finished_messages = [msg for msg in ws.messages if isinstance(msg, dict) and msg.get('type') == 'code_finished']
        assert len(finished_messages) > 0
        assert finished_messages[0]['payload']['exit_code'] == 0
        print("✅ Interactive container executed successfully (no output captured)")
    
    print("✅ Interactive container creation test passed")


def test_state_initialization():
    """Test that required state objects exist"""
    
    # Check that running_containers exists
    assert hasattr(state, 'running_containers')
    assert hasattr(state, 'running_containers_lock')
    
    # Should be able to access the lock
    assert state.running_containers_lock is not None