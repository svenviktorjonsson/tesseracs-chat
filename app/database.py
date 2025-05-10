# app/database.py
import sqlite3
import os
from pathlib import Path
import hashlib # For password hashing
import secrets # For generating secure tokens
import datetime # For timestamps

# Determine the project root directory based on the location of this file
# Assuming this file is app/database.py
APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent
DATABASE_NAME = "tesseracs_chat.db"
DATABASE_PATH = PROJECT_ROOT / DATABASE_NAME

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    # print(f"Attempting to connect to database at: {DATABASE_PATH}") # Uncomment for debugging
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row # Access columns by name
    # Enable foreign key constraints for this connection
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    """Initializes the database schema if it doesn't exist and adds new columns if needed."""
    print(f"Initializing database schema at {DATABASE_PATH}...")
    conn = get_db_connection()
    cursor = conn.cursor()

    # --- Users Table ---
    # Create table if it doesn't exist, including all columns (original + new)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE NOT NULL,
        selected_llm_provider_id TEXT,      -- User's chosen LLM provider
        selected_llm_model_id TEXT,         -- User's chosen LLM model
        user_llm_api_key_encrypted TEXT,    -- User's encrypted API key for their chosen provider
        selected_llm_base_url TEXT          -- User's custom base URL for compatible providers
    );
    """)
    print("Ensured 'users' table exists.")

    # Add new columns to 'users' table if they don't exist (for existing databases)
    user_table_columns_to_add = {
        "selected_llm_provider_id": "TEXT",
        "selected_llm_model_id": "TEXT",
        "user_llm_api_key_encrypted": "TEXT",
        "selected_llm_base_url": "TEXT"
    }
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [col[1] for col in cursor.fetchall()]

    for col_name, col_type in user_table_columns_to_add.items():
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                print(f"Added '{col_name}' column to 'users' table.")
            except sqlite3.OperationalError as e:
                # This might happen if the column was added in a previous partial run but schema wasn't fully committed
                # Or if there's a schema mismatch. For simple ADD COLUMN, it's usually safe.
                print(f"Could not add column {col_name} to users: {e}. It might already exist or there's a schema issue.")


    # --- Auth Tokens Table ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS auth_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        token_type TEXT NOT NULL CHECK(token_type IN ('magic_login', 'session', 'password_reset')),
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        used_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'auth_tokens' table exists.")

    # --- Sessions Table ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        host_user_id INTEGER NOT NULL,
        name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE NOT NULL,
        FOREIGN KEY (host_user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'sessions' table exists.")

    # --- Session Participants Table ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_participants (
        session_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (session_id, user_id),
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'session_participants' table exists.")

    # --- Chat Messages Table ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,       -- Links to the session
        user_id INTEGER,                -- Links to the user who sent it (NULL for AI/system)
        sender_name TEXT,               -- Display name of the sender (User's name, "AI", "System")
        sender_type TEXT NOT NULL CHECK(sender_type IN ('user', 'ai', 'system', 'anon_user')), -- Type of sender
        content TEXT NOT NULL,          -- The actual message content (Markdown, text)
        client_id_temp TEXT,            -- Temporary ID from client for optimistic updates (optional)
        turn_id INTEGER,                -- To group user message and AI response
        thinking_content TEXT,          -- AI's "thinking" process, if any
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL -- If user is deleted, message remains but user_id is NULL
    );
    """)
    print("Ensured 'chat_messages' table exists.")

    # --- Session Memory State Table ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_memory_state (
        session_id TEXT PRIMARY KEY,        -- Links directly to the session
        memory_state_json TEXT NOT NULL,    -- Stores the serialized memory state (e.g., as JSON)
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When the memory was last saved
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'session_memory_state' table exists.")

    # --- Password Reset Attempts Table ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,                -- The email for which the reset was attempted.
        ip_address TEXT,                    -- Optional: IP address of the requester.
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- Timestamp of the attempt.
    );
    """)
    print("Ensured 'password_reset_attempts' table exists.")


    # --- Indexes for Performance ---
    # Original indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_token_hash ON auth_tokens (token_hash);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_id ON auth_tokens (user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_host_user_id ON sessions (host_user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON session_participants (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_user_id ON session_participants (user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_memory_state_session_id ON session_memory_state (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_attempts_email_time ON password_reset_attempts (email, attempted_at);")
    print("Ensured all indexes exist.")

    conn.commit()
    conn.close()
    print("Database initialization process complete.")

def hash_value(value: str) -> str:
    """Hashes a string value (e.g., password or token) using SHA256."""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def generate_secure_token(length: int = 32) -> str:
    """Generates a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(length)

# You can run this file directly to initialize the database:
# python -m app.database
if __name__ == "__main__":
    print(f"Running database script directly. CWD: {Path.cwd()}")
    print(f"Project Root should be: {PROJECT_ROOT}")
    print(f"Database will be created/checked at: {DATABASE_PATH}")
    # Ensure the parent directory for the database exists
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db()
