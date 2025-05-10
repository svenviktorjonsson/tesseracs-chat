# app/config.py
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any

# Load environment variables from a .env file if it exists
load_dotenv()

# --- Application Base URL ---
BASE_URL = os.getenv("BASE_URL", "http://localhost:8001") 

# --- Secret Key for Encryption ---
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY")
if not APP_SECRET_KEY:
    print("CRITICAL WARNING: APP_SECRET_KEY is not set in the environment. "
          "User-specific API keys will not be securely stored. This is a major security risk. "
          "Please set this environment variable to a strong, random string. "
          "For development, the application will proceed, but encryption will be disabled if this key is missing.")
elif len(APP_SECRET_KEY) < 32: 
    print(f"WARNING: APP_SECRET_KEY is set but may not be strong enough (length: {len(APP_SECRET_KEY)}). "
          "Ensure it's a Fernet-compatible key (32 url-safe base64-encoded bytes).")


# --- LLM Configuration ---

ENV_DEFAULT_OLLAMA_MODEL_ID = os.getenv("DEFAULT_OLLAMA_MODEL_ID", "qwen3:8B") 
ENV_DEFAULT_OLLAMA_MODEL_DISPLAY_NAME = os.getenv("DEFAULT_OLLAMA_MODEL_DISPLAY_NAME", "Qwen3 8B (Local Default)")
ENV_DEFAULT_OLLAMA_MODEL_CONTEXT_WINDOW = int(os.getenv("DEFAULT_OLLAMA_MODEL_CONTEXT_WINDOW", 32768)) 

LLM_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "ollama_local": {
        "display_name": "Ollama (Local)",
        "type": "ollama",
        "base_url_env_var": "OLLAMA_BASE_URL", 
        "api_key_env_var": None, # Ollama does not use API keys in this manner
        "available_models": [
            { 
                "model_id": ENV_DEFAULT_OLLAMA_MODEL_ID, 
                "display_name": ENV_DEFAULT_OLLAMA_MODEL_DISPLAY_NAME,
                "context_window": ENV_DEFAULT_OLLAMA_MODEL_CONTEXT_WINDOW
            },
            {
                "model_id": "llama3:latest",
                "display_name": "Llama 3 (Local - if installed)",
                "context_window": 8192
            },
            {
                "model_id": "phi3:latest",
                "display_name": "Phi-3 (Local - if installed)",
                "context_window": 4096 
            },
        ],
    },
    "google_gemini": {
        "display_name": "Google Gemini",
        "type": "google_genai", 
        "base_url_env_var": None, # Google SDK handles the endpoint
        "api_key_env_var": None, # API key MUST come from user settings
        "available_models": [
            {
                "model_id": "gemini-2.0-flash", # User specified model ID
                "display_name": "Gemini 2.0 Flash", # Using user's preference
                "context_window": 1048576 # Assuming 1M, verify if specific to "2.0-flash"
            },
            {
                "model_id": "gemini-1.5-pro-latest", 
                "display_name": "Gemini 1.5 Pro", # Kept as an alternative
                "context_window": 1048576 
            }
        ],
    },
    "anthropic_claude": {
        "display_name": "Anthropic Claude",
        "type": "anthropic", 
        "base_url_env_var": None, # Anthropic SDK handles the endpoint
        "api_key_env_var": None, # API key MUST come from user settings
        "available_models": [
            { 
                "model_id": "claude-3-7-sonnet-20250219", # User specified model ID
                "display_name": "Claude 3.7 Sonnet", # Using user's preference
                "context_window": 200000 # Standard Sonnet context
            },
            {
                "model_id": "claude-3-opus-20240229", # Kept as an alternative
                "display_name": "Claude 3 Opus",
                "context_window": 200000
            },
            {
                "model_id": "claude-3-haiku-20240307", # Kept as an alternative
                "display_name": "Claude 3 Haiku",
                "context_window": 200000
            }
        ],
    },
    "openai_compatible_server": { 
        "display_name": "OpenAI-Compatible API", 
        "type": "openai_compatible",
        "base_url_env_var": "OPENAI_COMPATIBLE_BASE_URL", 
        "api_key_env_var": None, # API key MUST come from user settings for this provider too
        "available_models": [
            {
                "model_id": "gpt-4o", 
                "display_name": "GPT-4o (OpenAI Compatible)",
                "context_window": 128000 
            },
            {
                "model_id": "gpt-3.5-turbo",
                "display_name": "GPT-3.5 Turbo (OpenAI Compatible)",
                "context_window": 16385
            }
        ],
    },
}

# --- Default LLM Selection ---
DEFAULT_LLM_PROVIDER_ID: str = "ollama_local" 
DEFAULT_LLM_MODEL_ID: str = ENV_DEFAULT_OLLAMA_MODEL_ID

# --- Helper function to get a provider's configuration ---
def get_provider_config(provider_id: str) -> Optional[Dict[str, Any]]:
    provider_info_template = LLM_PROVIDERS.get(provider_id)
    if not provider_info_template:
        return None

    runtime_config = provider_info_template.copy()
    
    base_url_env_name = provider_info_template.get("base_url_env_var")
    if base_url_env_name: 
        runtime_config["base_url"] = os.getenv(base_url_env_name)
    else: 
        runtime_config["base_url"] = provider_info_template.get("base_url")

    if provider_info_template.get("type") == "ollama":
        ollama_specific_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") 
        runtime_config["base_url"] = ollama_specific_base_url

    runtime_config["api_key_env_var_name"] = provider_info_template.get("api_key_env_var")
    
    if "available_models" not in runtime_config or not isinstance(runtime_config["available_models"], list):
        runtime_config["available_models"] = []
    return runtime_config

# --- Ollama Base URL ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# --- Chat Behavior Prefixes ---
NO_THINK_PREFIX = "\\no_think"
THINK_PREFIX = "\\think"

# --- Docker Configuration ---
SUPPORTED_LANGUAGES = {
    "python": {"image": "python:3.11-slim", "filename": "script.py", "command": ["python", "-u", "/app/script.py"]},
    "javascript": {"image": "node:18-alpine", "filename": "script.js", "command": ["node", "/app/script.js"]},
    "cpp": {"image": "gcc:latest", "filename": "script.cpp", "command": ["sh", "-c", "g++ /app/script.cpp -o /app/output_executable && /app/output_executable"]},
    "csharp": {"image": "mcr.microsoft.com/dotnet/sdk:latest", "filename": "Script.cs", "command": ["sh", "-c", "cd /app && dotnet new console --force -o . > /dev/null && cp Script.cs Program.cs && rm Script.cs && dotnet run"]},
    "typescript": {"image": "node:18-alpine", "filename": "script.ts", "command": ["sh", "-c", "npm install -g typescript > /dev/null 2>&1 && tsc --module commonjs /app/script.ts && node /app/script.js"]},
    "java": {"image": "openjdk:17-jdk-slim", "filename": "Main.java", "command": ["sh", "-c", "javac /app/Main.java && java -cp /app Main"]},
    "go": {"image": "golang:1.21-alpine", "filename": "script.go", "command": ["go", "run", "/app/script.go"]},
    "rust": {"image": "rust:1-slim", "filename": "main.rs", "command": ["sh", "-c", "cd /app && rustc main.rs -o main_executable && ./main_executable"]}
}
DOCKER_TIMEOUT_SECONDS = int(os.getenv("DOCKER_TIMEOUT_SECONDS", 30))
DOCKER_MEM_LIMIT = os.getenv("DOCKER_MEM_LIMIT", "128m")

# --- Static Files Configuration ---
APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR_IN_APP = APP_DIR / "static"
STATIC_DIR_AT_ROOT_LEVEL = PROJECT_ROOT / "static" 
STATIC_DIR = None
if STATIC_DIR_IN_APP.is_dir():
    STATIC_DIR = STATIC_DIR_IN_APP
elif STATIC_DIR_AT_ROOT_LEVEL.is_dir():
    STATIC_DIR = STATIC_DIR_AT_ROOT_LEVEL
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

if not all([MAIL_CONFIG["MAIL_USERNAME"], MAIL_CONFIG["MAIL_PASSWORD"], MAIL_CONFIG["MAIL_SERVER"], MAIL_CONFIG["MAIL_FROM"]]):
    print("WARNING: Essential email configuration (USERNAME, PASSWORD, SERVER, FROM) missing in .env file. Email functionalities will likely fail.")

# --- Rate Limiting Configuration ---
FORGOT_PASSWORD_ATTEMPT_LIMIT = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_LIMIT", 3))
FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS", 24))


# --- Debug Logging for Configuration ---
print(f"DEBUG config: Application BASE_URL is set to: {BASE_URL}")
print(f"DEBUG config: Static files directory resolved to: {STATIC_DIR}")
print(f"DEBUG config: Email VALIDATE_CERTS: {MAIL_CONFIG['VALIDATE_CERTS']}, SSL_TLS: {MAIL_CONFIG['MAIL_SSL_TLS']}, STARTTLS: {MAIL_CONFIG['MAIL_STARTTLS']}")
print(f"DEBUG config: Default LLM Provider ID (system default): {DEFAULT_LLM_PROVIDER_ID}")
print(f"DEBUG config: Default LLM Model ID (system default, from ENV for Ollama): {DEFAULT_LLM_MODEL_ID}")

default_provider_runtime_details = get_provider_config(DEFAULT_LLM_PROVIDER_ID)
if default_provider_runtime_details:
    print(f"DEBUG config: Default Provider Type: {default_provider_runtime_details.get('type')}")
    print(f"DEBUG config: Default Provider Resolved Base URL: {default_provider_runtime_details.get('base_url')}")
    print(f"DEBUG config: Default Provider API Key Env Var Name (should be None for non-Ollama if no .env fallback desired): {default_provider_runtime_details.get('api_key_env_var_name')}") 
else:
    print(f"WARNING config: Default LLM Provider '{DEFAULT_LLM_PROVIDER_ID}' could not be resolved by get_provider_config.")

