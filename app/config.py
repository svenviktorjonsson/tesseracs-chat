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

CSRF_PROTECT_SECRET_KEY = os.getenv("CSRF_PROTECT_SECRET_KEY")

DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ('true', '1', 't', 'yes')
DATABASE_PATH = Path(os.getenv("DATABASE_PATH","./tesseracs_chat.db")) # Corrected typo from DATABSE_PATH
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
        ],
    },
    "google_gemini": {
        "display_name": "Google Gemini",
        "type": "google_genai",
        "base_url_env_var": None, # Google SDK handles the endpoint
        "api_key_env_var": "GOOGLE_API_KEY", # User's original: None, changed to GOOGLE_API_KEY to align with get_provider_config usage
        "available_models": [
            {
                "model_id": "gemini-2.5-pro-preview-05-06", # User's original model ID
                "display_name": "Gemini 2.5 Pro", 
                "context_window": 1048576 
            },
            {
                "model_id": "gemini-2.5-flash-preview-04-17", # User's original model ID
                "display_name": "Gemini 2.5 Flash", 
                "context_window": 1048576 
            }

        ],
    },
    "anthropic_claude": {
        "display_name": "Anthropic Claude",
        "type": "anthropic",
        "base_url_env_var": None, # Anthropic SDK handles the endpoint
        "api_key_env_var": "ANTHROPIC_API_KEY", # User's original: None, changed to ANTHROPIC_API_KEY
        "available_models": [
            {
                "model_id": "claude-3-7-sonnet-20250219", # User's original model ID
                "display_name": "Claude 3.7 Sonnet", 
                "context_window": 200000 
            }
        ],
    },
    "openai_compatible_server": {
        "display_name": "OpenAI-Compatible API",
        "type": "openai_compatible",
        "base_url_env_var": "OPENAI_COMPATIBLE_BASE_URL",
        "api_key_env_var": "OPENAI_COMPATIBLE_API_KEY", # User's original: None, changed to OPENAI_COMPATIBLE_API_KEY
        "available_models": [
            {
                "model_id": "o4-mini", # User's original model ID
                "display_name": "o4 mini (OpenAI Compatible)",
                "context_window": 128000
            }
        ],
    },
}

# --- Default LLM Selection ---
DEFAULT_LLM_PROVIDER_ID: str = os.getenv("DEFAULT_LLM_PROVIDER_ID", "ollama_local") # User's original default
# Logic to set DEFAULT_LLM_MODEL_ID based on DEFAULT_LLM_PROVIDER_ID and its available models
_default_provider_config_for_model_fallback = LLM_PROVIDERS.get(DEFAULT_LLM_PROVIDER_ID, {})
_default_provider_models_for_fallback = _default_provider_config_for_model_fallback.get("available_models", [])
_default_model_id_env_candidate = os.getenv("DEFAULT_LLM_MODEL_ID")

if _default_model_id_env_candidate and any(m["model_id"] == _default_model_id_env_candidate for m in _default_provider_models_for_fallback):
    DEFAULT_LLM_MODEL_ID: str = _default_model_id_env_candidate
elif _default_provider_models_for_fallback:
    DEFAULT_LLM_MODEL_ID: str = _default_provider_models_for_fallback[0]["model_id"]
else:
    DEFAULT_LLM_MODEL_ID: str = "qwen3:8B" # Fallback to user's original hardcoded default if provider/models not found
    if DEFAULT_LLM_PROVIDER_ID == "ollama_local":
        DEFAULT_LLM_MODEL_ID = ENV_DEFAULT_OLLAMA_MODEL_ID # Ensure it uses the ollama default if provider is ollama
    print(f"WARNING: Could not determine a valid DEFAULT_LLM_MODEL_ID for provider '{DEFAULT_LLM_PROVIDER_ID}' from its 'available_models'. Using '{DEFAULT_LLM_MODEL_ID}'.")


# --- Provider Characteristics ---
# ADDED THESE AS THEY WERE MISSING AND CAUSING AttributeErrors
PROVIDERS_TYPICALLY_USING_API_KEYS = {
    "google_gemini",
    "anthropic_claude",
    "openai_compatible_server"
    # Add "openai" here if you add a direct OpenAI provider definition
}

PROVIDERS_ALLOWING_USER_KEYS_EVEN_IF_SYSTEM_CONFIGURED = {
    "google_gemini",                # User might want to use their own project quota/key
    "anthropic_claude",             # User might want to use their own key
    "openai_compatible_server"      # For this type, user often provides key and base_url
}


# --- Helper function to get a provider's configuration ---
def get_provider_config(provider_id: str) -> Optional[Dict[str, Any]]:
    provider_info_template = LLM_PROVIDERS.get(provider_id)
    if not provider_info_template:
        return None

    runtime_config = provider_info_template.copy()

    base_url_env_name = provider_info_template.get("base_url_env_var")
    resolved_base_url = None # Initialize
    if base_url_env_name:
        resolved_base_url = os.getenv(base_url_env_name)
    
    # Specific handling for Ollama's common OLLAMA_BASE_URL if its own provider-specific env var isn't set
    if provider_info_template.get("type") == "ollama" and not resolved_base_url:
        resolved_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    
    runtime_config["base_url"] = resolved_base_url if resolved_base_url else provider_info_template.get("base_url") # Fallback to static base_url in template if any

    # This key in runtime_config is what main.py's list_llm_providers expects
    runtime_config["api_key_env_var_name"] = provider_info_template.get("api_key_env_var")

    if "available_models" not in runtime_config or not isinstance(runtime_config["available_models"], list):
        runtime_config["available_models"] = []
    return runtime_config

# --- Ollama Base URL (General fallback if not specified per provider) ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# --- Chat Behavior Prefixes ---
NO_THINK_PREFIX = "\\no_think"
THINK_PREFIX = "\\think"

# --- Docker Configuration ---
SUPPORTED_LANGUAGES = {
    "python": {"image": "python:3.11-slim", "filename": "script.py", "command": ["python", "-u", "/app/script.py"]},
    "javascript": {"image": "node:18-alpine", "filename": "script.js", "command": ["node", "/app/script.js"]},
    "cpp": {"image": "gcc:latest", "filename": "script.cpp", "command": ["sh", "-c", "g++ /app/script.cpp -o /app/output_executable && exec /app/output_executable"]},
    "csharp": {"image": "mcr.microsoft.com/dotnet/sdk:latest", "filename": "Script.cs", "command": ["sh", "-c", "cd /app && dotnet new console --force -o . > /dev/null && cp Script.cs Program.cs && rm Script.cs && exec dotnet run"]},
    "typescript": {"image": "node:18-alpine", "filename": "script.ts", "command": ["sh", "-c", "npm install -g typescript > /dev/null 2>&1 && tsc --module commonjs /app/script.ts && exec node /app/script.js"]},
    "java": {"image": "openjdk:17-jdk-slim", "filename": "Main.java", "command": ["sh", "-c", "javac /app/Main.java && exec java -cp /app Main"]},
    "go": {"image": "golang:1.21-alpine", "filename": "script.go", "command": ["go", "run", "/app/script.go"]},
    "rust": {"image": "rust:1-slim", "filename": "main.rs", "command": ["sh", "-c", "cd /app && rustc main.rs -o main_executable && exec ./main_executable"]}
}

DOCKER_TIMEOUT_SECONDS = int(os.getenv("DOCKER_TIMEOUT_SECONDS", 30))
DOCKER_MEM_LIMIT = os.getenv("DOCKER_MEM_LIMIT", "128m")

# --- Static Files Configuration ---
APP_DIR = Path(__file__).resolve().parent 
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR_IN_APP = APP_DIR / "static"
STATIC_DIR_AT_ROOT_LEVEL = PROJECT_ROOT / "static"
STATIC_DIR: Optional[Path] = None 

if STATIC_DIR_IN_APP.is_dir():
    STATIC_DIR = STATIC_DIR_IN_APP
elif STATIC_DIR_AT_ROOT_LEVEL.is_dir() and (PROJECT_ROOT / "pyproject.toml").exists(): 
    STATIC_DIR = STATIC_DIR_AT_ROOT_LEVEL
else:
    current_working_dir = Path.cwd()
    if (current_working_dir / "app" / "static").is_dir(): 
        STATIC_DIR = current_working_dir / "app" / "static"
    elif (current_working_dir / "static").is_dir(): 
        STATIC_DIR = current_working_dir / "static"
    
    if not STATIC_DIR or not STATIC_DIR.is_dir(): 
        print(f"CRITICAL ERROR: Static directory not found. Checked standard locations relative to {APP_DIR}, {PROJECT_ROOT}, and {current_working_dir}. Application may not serve frontend assets.")
        # sys.exit(1) # Consider if exiting is appropriate or just log error

# --- Email Configuration ---
MAIL_CONFIG = {
    "MAIL_USERNAME": os.getenv("MAIL_USERNAME"),
    "MAIL_PASSWORD": os.getenv("MAIL_PASSWORD"),
    "MAIL_FROM": os.getenv("MAIL_FROM"),
    "MAIL_PORT": int(os.getenv("MAIL_PORT", 465)),
    "MAIL_SERVER": os.getenv("MAIL_SERVER"),
    "MAIL_FROM_NAME": os.getenv("MAIL_FROM_NAME", "Tesseracs Chat"),
    "MAIL_STARTTLS": os.getenv("MAIL_STARTTLS", 'False').lower() in ('true', '1', 't', 'yes'),
    "MAIL_SSL_TLS": os.getenv("MAIL_SSL_TLS", 'True').lower() in ('true', '1', 't', 'yes'),
    "USE_CREDENTIALS": True,
    "VALIDATE_CERTS": os.getenv("MAIL_VALIDATE_CERTS", 'True').lower() in ('true', '1', 't', 'yes')
}

if not all([MAIL_CONFIG["MAIL_USERNAME"], MAIL_CONFIG["MAIL_PASSWORD"], MAIL_CONFIG["MAIL_SERVER"], MAIL_CONFIG["MAIL_FROM"]]):
    print("WARNING: Essential email configuration (USERNAME, PASSWORD, SERVER, FROM) missing in .env file. Email functionalities will likely fail.")

# --- Rate Limiting Configuration ---
FORGOT_PASSWORD_ATTEMPT_LIMIT = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_LIMIT", 3)) # User's original
FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS", 24)) # User's original

# --- Debug Logging for Configuration ---
print(f"DEBUG config: Application DEBUG_MODE: {DEBUG_MODE}")
print(f"DEBUG config: Application BASE_URL is set to: {BASE_URL}")
if STATIC_DIR:
    print(f"DEBUG config: Static files directory resolved to: {STATIC_DIR.resolve()}")
else:
     print("DEBUG config: Static files directory NOT RESOLVED.")
print(f"DEBUG config: Email VALIDATE_CERTS: {MAIL_CONFIG['VALIDATE_CERTS']}, SSL_TLS: {MAIL_CONFIG['MAIL_SSL_TLS']}, STARTTLS: {MAIL_CONFIG['MAIL_STARTTLS']}")
print(f"DEBUG config: Default LLM Provider ID (system default): {DEFAULT_LLM_PROVIDER_ID}")
print(f"DEBUG config: Default LLM Model ID (system default): {DEFAULT_LLM_MODEL_ID}")

default_provider_runtime_details = get_provider_config(DEFAULT_LLM_PROVIDER_ID)
if default_provider_runtime_details:
    print(f"DEBUG config: Default Provider Type: {default_provider_runtime_details.get('type')}")
    print(f"DEBUG config: Default Provider Resolved Base URL: {default_provider_runtime_details.get('base_url')}")
    print(f"DEBUG config: Default Provider API Key Env Var Name (from LLM_PROVIDERS 'api_key_env_var'): {default_provider_runtime_details.get('api_key_env_var_name')}")
else:
    print(f"WARNING config: Default LLM Provider '{DEFAULT_LLM_PROVIDER_ID}' could not be resolved by get_provider_config.")

if not CSRF_PROTECT_SECRET_KEY:
     print("WARNING config: CSRF_PROTECT_SECRET_KEY is not set in environment. main.py will use a fallback (insecure for production).")
