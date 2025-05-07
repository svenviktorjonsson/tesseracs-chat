# app/config.py
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

# --- LLM Configuration ---
# Model ID for the Ollama language model
MODEL_ID = os.getenv("MODEL_ID", "qwen3:8B")
# Base URL for the Ollama API server
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# Prefix used by the frontend to signal non-thinking requests (handled by frontend)
NO_THINK_PREFIX = "\\no_think"

# --- Docker Configuration ---
# Dictionary defining supported languages for code execution.
# Each key is the language identifier (lowercase).
# Each value is a dictionary containing:
#   - image: The Docker image to use for execution.
#   - filename: The expected filename for the code snippet inside the container.
#   - command: The command list to execute the code within the container.
SUPPORTED_LANGUAGES = {
    # --- Existing Languages ---
    "python": {
        "image": "python:3.11-slim",
        "filename": "script.py",
        # Run python with unbuffered output (-u)
        "command": ["python", "-u", "/app/script.py"]
    },
    "javascript": {
        # --- UPDATED IMAGE ---
        "image": "node-ts:18", # Use standard Node.js 18 image (same as TypeScript)
        "filename": "script.js",
        "command": ["node", "/app/script.js"]
    },
    # --- Added Languages ---
    "cpp": {
        "image": "gcc:latest", # GNU C++ compiler image
        "filename": "script.cpp",
        # Command compiles script.cpp to an executable, then runs it.
        # Uses 'sh -c' to chain the compilation (g++) and execution steps.
        "command": ["sh", "-c", "g++ /app/script.cpp -o /app/output_executable && /app/output_executable"]
    },
    "csharp": {
        "image": "mcr.microsoft.com/dotnet/sdk:latest",
        "filename": "Script.cs", # Backend saves user code as Script.cs
        # ------ UPDATED COMMAND TO FIX DUPLICATE DEFINITION ERRORS ------
        # Creates project, copies user code over Program.cs,
        # REMOVES the original Script.cs, then runs.
        "command": [
            "sh", "-c",
            "cd /app && dotnet new console --force -o . > /dev/null && cp Script.cs Program.cs && rm Script.cs && dotnet run"
        ]
        # ------ END UPDATED COMMAND ------
    },
    "typescript": {
        "image": "node-ts:18", # Use standard Node.js 18 image (Debian based, includes npm/npx)
        "filename": "script.ts",
        # Compile script.ts to script.js targeting CommonJS, then run with node.
        # Assumes 'tsc' (TypeScript compiler) is available in the 'node:18' image.
        # If 'tsc' is not found, you may need to revert to the previous command with 'npm install'
        # or build a custom image with typescript pre-installed.
        "command": ["sh", "-c", "tsc --module commonjs /app/script.ts && node /app/script.js"]
    },
    "java": {
        "image": "openjdk:17-jdk-slim", # Java Development Kit image
        "filename": "Main.java", # Java requires filename match public class name
        # Command compiles Main.java and then runs the compiled Main class.
        "command": ["sh", "-c", "javac /app/Main.java && java -cp /app Main"]
    },
    "go": {
        "image": "golang:1.21-alpine", # Go language image
        "filename": "script.go",
        # Command uses 'go run' to compile and run the source file directly.
        "command": ["go", "run", "/app/script.go"]
    },
    "rust": {
        "image": "rust:1-slim", # Rust language image
        "filename": "main.rs", # Rust convention often uses main.rs
        # Command compiles main.rs to an executable, then runs it.
        "command": ["sh", "-c", "cd /app && rustc main.rs -o main_executable && ./main_executable"]
    }
    # Add more languages here following the pattern:
    # "language_name": { "image": "docker_image", "filename": "script_name", "command": ["command", "arg1", ...]}
}
# Timeout in seconds for Docker container execution
DOCKER_TIMEOUT_SECONDS = 30
# Memory limit for Docker containers (e.g., "128m", "256m")
DOCKER_MEM_LIMIT = "128m"

# --- Static Files Configuration ---
# Determine the static files directory relative to this config file's location.
# Assumes config.py is inside the 'app' directory.
APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR_IN_APP = APP_DIR / "static"
STATIC_DIR_AT_ROOT_LEVEL = PROJECT_ROOT / "static" # If static is sibling to 'app'

STATIC_DIR = None
# Check for static directory inside 'app' first
if STATIC_DIR_IN_APP.is_dir():
    STATIC_DIR = STATIC_DIR_IN_APP
    # print(f"Found static directory at: {STATIC_DIR}") # Uncomment for debug
# If not found inside 'app', check at the project root level (sibling to 'app')
elif STATIC_DIR_AT_ROOT_LEVEL.is_dir():
     STATIC_DIR = STATIC_DIR_AT_ROOT_LEVEL
     # print(f"Found static directory at: {STATIC_DIR}") # Uncomment for debug
# If neither location is found, print an error and exit
else:
    print(f"CRITICAL ERROR: Static directory not found. Looked in '{STATIC_DIR_IN_APP}' and '{STATIC_DIR_AT_ROOT_LEVEL}'. Exiting.")
    sys.exit(1)

