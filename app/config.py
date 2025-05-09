# app/config.py
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

# --- Application Base URL ---
# This is the primary public-facing URL for your application.
# Used for generating absolute URLs in emails, etc.
# Ensure this matches your production domain and includes the scheme (http or https).
# It can be overridden by setting a BASE_URL environment variable.
BASE_URL = os.getenv("BASE_URL", "http://tesseracs.com") # Default to http://tesseracs.com

# --- LLM Configuration ---
MODEL_ID = os.getenv("MODEL_ID", "qwen3:8B")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") # This is for the Ollama service
NO_THINK_PREFIX = "\\no_think"
THINK_PREFIX = "\\think"

# --- Docker Configuration ---
SUPPORTED_LANGUAGES = {
    "python": {
        "image": "python:3.11-slim",
        "filename": "script.py",
        "command": ["python", "-u", "/app/script.py"]
    },
    "javascript": {
        "image": "node:18-alpine",
        "filename": "script.js",
        "command": ["node", "/app/script.js"]
    },
    "cpp": {
        "image": "gcc:latest",
        "filename": "script.cpp",
        "command": ["sh", "-c", "g++ /app/script.cpp -o /app/output_executable && /app/output_executable"]
    },
    "csharp": {
        "image": "mcr.microsoft.com/dotnet/sdk:latest",
        "filename": "Script.cs",
        "command": [
            "sh", "-c",
            "cd /app && dotnet new console --force -o . > /dev/null && cp Script.cs Program.cs && rm Script.cs && dotnet run"
        ]
    },
    "typescript": {
        "image": "node:18-alpine",
        "filename": "script.ts",
        "command": ["sh", "-c", "tsc --module commonjs /app/script.ts && node /app/script.js"]
    },
    "java": {
        "image": "openjdk:17-jdk-slim",
        "filename": "Main.java",
        "command": ["sh", "-c", "javac /app/Main.java && java -cp /app Main"]
    },
    "go": {
        "image": "golang:1.21-alpine",
        "filename": "script.go",
        "command": ["go", "run", "/app/script.go"]
    },
    "rust": {
        "image": "rust:1-slim",
        "filename": "main.rs",
        "command": ["sh", "-c", "cd /app && rustc main.rs -o main_executable && ./main_executable"]
    }
}
DOCKER_TIMEOUT_SECONDS = int(os.getenv("DOCKER_TIMEOUT_SECONDS", 30))
DOCKER_MEM_LIMIT = os.getenv("DOCKER_MEM_LIMIT", "128m")

# --- Static Files Configuration ---
APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR_IN_APP = APP_DIR / "static"
STATIC_DIR_AT_ROOT_LEVEL = PROJECT_ROOT / "static"
STATIC_DIR = None
if STATIC_DIR_IN_APP.is_dir(): STATIC_DIR = STATIC_DIR_IN_APP
elif STATIC_DIR_AT_ROOT_LEVEL.is_dir(): STATIC_DIR = STATIC_DIR_AT_ROOT_LEVEL
else:
    print(f"CRITICAL ERROR: Static directory not found. Looked in '{STATIC_DIR_IN_APP}' and '{STATIC_DIR_AT_ROOT_LEVEL}'. Exiting.")
    sys.exit(1)

# --- Email Configuration ---
MAIL_CONFIG = {
    "MAIL_USERNAME": os.getenv("MAIL_USERNAME"),
    "MAIL_PASSWORD": os.getenv("MAIL_PASSWORD"),
    "MAIL_FROM": os.getenv("MAIL_FROM"),
    "MAIL_PORT": int(os.getenv("MAIL_PORT", 465)),
    "MAIL_SERVER": os.getenv("MAIL_SERVER"),
    "MAIL_FROM_NAME": os.getenv("MAIL_FROM_NAME", "Tesseracs Chat"),
    "MAIL_STARTTLS": os.getenv("MAIL_STARTTLS", 'False').lower() in ('true', '1', 't'),
    "MAIL_SSL_TLS": os.getenv("MAIL_SSL_TLS", 'True').lower() in ('true', '1', 't'),
    "USE_CREDENTIALS": True,
    "VALIDATE_CERTS": os.getenv("MAIL_VALIDATE_CERTS", 'True').lower() in ('true', '1', 't')
}

if not all([MAIL_CONFIG["MAIL_USERNAME"], MAIL_CONFIG["MAIL_PASSWORD"], MAIL_CONFIG["MAIL_SERVER"]]):
    print("WARNING: Essential email configuration missing in .env file.")

print(f"DEBUG config: Email certificate validation (VALIDATE_CERTS) is set to: {MAIL_CONFIG['VALIDATE_CERTS']}")
print(f"DEBUG config: Application BASE_URL is set to: {BASE_URL}") # Added log for BASE_URL


# --- Rate Limiting Configuration ---
FORGOT_PASSWORD_ATTEMPT_LIMIT = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_LIMIT", 3))
FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS", 24))
