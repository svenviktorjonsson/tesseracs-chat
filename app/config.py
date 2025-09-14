import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any
import json

# Load environment variables from a .env file if it exists
load_dotenv()

# --- Application Base URL ---
BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")

CSRF_PROTECT_SECRET_KEY = os.getenv("CSRF_PROTECT_SECRET_KEY")

DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ('true', '1', 't', 'yes')
DATABASE_PATH = Path(os.getenv("DATABASE_PATH","./tesseracs_chat.db"))

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
LLM_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "google_gemini": {
        "display_name": "Google Gemini",
        "type": "google_genai",
        "base_url_env_var": None, # Google SDK handles the endpoint
        "api_key_env_var": "GOOGLE_API_KEY",
        "available_models": [
            {
                "model_id": "gemini-2.5-pro",
                "display_name": "Gemini 2.5 Pro", 
                "context_window": 1048576 
            },
            {
                "model_id": "gemini-2.5-flash",
                "display_name": "Gemini 2.5 Flash", 
                "context_window": 1048576 
            }
        ],
    },
    "anthropic_claude": {
        "display_name": "Anthropic Claude",
        "type": "anthropic",
        "base_url_env_var": None, # Anthropic SDK handles the endpoint
        "api_key_env_var": "ANTHROPIC_API_KEY",
        "available_models": [
            {
                "model_id": "claude-sonnet-4-20250514",
                "display_name": "Claude 4.0 Sonnet", 
                "context_window": 200000 
            }
        ],
    },
    "openai_compatible_server": {
        "display_name": "OpenAI-Compatible API",
        "type": "openai_compatible",
        "base_url_env_var": "OPENAI_COMPATIBLE_BASE_URL",
        "api_key_env_var": "OPENAI_COMPATIBLE_API_KEY",
        "available_models": [
            {
                "model_id": "gpt-4o-2024-08-06", 
                "display_name": "GPT 4o",
                "context_window": 128000
            }
        ],
    },
}

# --- Provider Characteristics ---
PROVIDERS_TYPICALLY_USING_API_KEYS = {
    "google_gemini",
    "anthropic_claude",
    "openai_compatible_server"
}

PROVIDERS_ALLOWING_USER_KEYS_EVEN_IF_SYSTEM_CONFIGURED = {
    "google_gemini",
    "anthropic_claude",
    "openai_compatible_server"
}


# --- Helper function to get a provider's configuration ---
def get_provider_config(provider_id: str) -> Optional[Dict[str, Any]]:
    provider_info_template = LLM_PROVIDERS.get(provider_id)
    if not provider_info_template:
        return None

    runtime_config = provider_info_template.copy()

    base_url_env_name = provider_info_template.get("base_url_env_var")
    resolved_base_url = None
    if base_url_env_name:
        resolved_base_url = os.getenv(base_url_env_name)
    
    runtime_config["base_url"] = resolved_base_url if resolved_base_url else provider_info_template.get("base_url")

    runtime_config["api_key_env_var_name"] = provider_info_template.get("api_key_env_var")

    if "available_models" not in runtime_config or not isinstance(runtime_config["available_models"], list):
        runtime_config["available_models"] = []
    return runtime_config


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

# --- Docker Configuration ---
LANGUAGES_CONFIG_PATH = APP_DIR / "static" / "languages.json"
try:
    with open(LANGUAGES_CONFIG_PATH, "r", encoding="utf-8") as f:
        SUPPORTED_LANGUAGES = json.load(f)
    print(f"DEBUG config: Successfully loaded {len(SUPPORTED_LANGUAGES)} languages from {LANGUAGES_CONFIG_PATH}")
except Exception as e:
    print(f"CRITICAL ERROR: Could not load or parse languages.json: {e}")
    SUPPORTED_LANGUAGES = {}

DOCKER_TIMEOUT_SECONDS = int(os.getenv("DOCKER_TIMEOUT_SECONDS", 30))
DOCKER_MEM_LIMIT = os.getenv("DOCKER_MEM_LIMIT", "128m")


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
FORGOT_PASSWORD_ATTEMPT_LIMIT = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_LIMIT", 3))
FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS = int(os.getenv("FORGOT_PASSWORD_ATTEMPT_WINDOW_HOURS", 24))

# --- Debug Logging for Configuration ---
print(f"DEBUG config: Application DEBUG_MODE: {DEBUG_MODE}")
print(f"DEBUG config: Application BASE_URL is set to: {BASE_URL}")
if STATIC_DIR:
    print(f"DEBUG config: Static files directory resolved to: {STATIC_DIR.resolve()}")
else:
     print("DEBUG config: Static files directory NOT RESOLVED.")
print(f"DEBUG config: Email VALIDATE_CERTS: {MAIL_CONFIG['VALIDATE_CERTS']}, SSL_TLS: {MAIL_CONFIG['MAIL_SSL_TLS']}, STARTTLS: {MAIL_CONFIG['MAIL_STARTTLS']}")

if not CSRF_PROTECT_SECRET_KEY:
     print("WARNING config: CSRF_PROTECT_SECRET_KEY is not set in environment. main.py will use a fallback (insecure for production).")
 
print("-" * 50)
print(f"DEBUG: APP_SECRET_KEY loaded in config.py: {APP_SECRET_KEY}")
print("-" * 50)
