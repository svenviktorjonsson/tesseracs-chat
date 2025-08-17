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
DATABASE_NAME = "tesseracs_chat.db" # Name of the SQLite database file
DATABASE_PATH = PROJECT_ROOT / DATABASE_NAME # Full path to the database file

def get_code_execution_results(session_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT code_block_id, language, code_content, output_content, 
                   html_content, exit_code, error_message, execution_status, 
                   executed_at, turn_id
            FROM code_execution_results 
            WHERE session_id = ? 
            ORDER BY executed_at ASC
        """, (session_id,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'code_block_id': row['code_block_id'],
                'language': row['language'],
                'code_content': row['code_content'],
                'output_content': row['output_content'],
                'html_content': row['html_content'],
                'exit_code': row['exit_code'],
                'error_message': row['error_message'],
                'execution_status': row['execution_status'],
                'executed_at': row['executed_at'],
                'turn_id': row['turn_id']
            })
        
        return results
    except Exception as e:
        print(f"Error retrieving code execution results for session {session_id}: {e}")
        return []
    finally:
        conn.close()

def save_code_execution_result(session_id, code_block_id, language, code_content, 
                             output_content=None, html_content=None, exit_code=None, 
                             error_message=None, execution_status='completed', turn_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM code_execution_results WHERE code_block_id = ?", (code_block_id,))
        
        cursor.execute("""
            INSERT INTO code_execution_results 
            (session_id, code_block_id, language, code_content, output_content, 
             html_content, exit_code, error_message, execution_status, turn_id, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
        """, (session_id, code_block_id, language, code_content, output_content, 
              html_content, exit_code, error_message, execution_status, turn_id))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error saving code execution result for {code_block_id}: {e}")
        return False
    finally:
        conn.close()

def get_db_connection():
    """
    Establishes a connection to the SQLite database.
    Enables row_factory for column access by name and foreign key constraints.
    """
    # print(f"Attempting to connect to database at: {DATABASE_PATH}") # Uncomment for debugging path issues
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row # Allows accessing columns by name (e.g., row['email'])
    conn.execute("PRAGMA foreign_keys = ON;") # Enforce foreign key constraints for this connection
    return conn

def init_db():
    """
    Initializes the database schema.
    Creates tables if they don't exist and attempts to add missing columns
    to existing tables to support schema evolution.
    """
    print(f"Initializing database schema at {DATABASE_PATH}...")
    conn = get_db_connection()
    cursor = conn.cursor()

    # --- Users Table ---
    # Defines user accounts, their credentials, and LLM preferences.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- Added for tracking updates
        is_active BOOLEAN DEFAULT TRUE NOT NULL,
        selected_llm_provider_id TEXT,      -- User's chosen LLM provider ID
        selected_llm_model_id TEXT,         -- User's chosen LLM model ID for the selected provider
        user_llm_api_key_encrypted TEXT,    -- User's encrypted API key (if they provide their own)
        selected_llm_base_url TEXT          -- User's custom base URL for compatible LLM providers (e.g., OpenAI-compatible servers)
    );
    """)
    print("Ensured 'users' table exists.")

    # Add new columns to 'users' table if they don't exist (for existing databases)
    user_table_columns_to_add = {
        "selected_llm_provider_id": "TEXT",
        "selected_llm_model_id": "TEXT",
        "user_llm_api_key_encrypted": "TEXT",
        "selected_llm_base_url": "TEXT",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP" # Ensure updated_at can be added
    }
    cursor.execute("PRAGMA table_info(users)")
    existing_user_columns = [col[1] for col in cursor.fetchall()]

    for col_name, col_definition in user_table_columns_to_add.items():
        if col_name not in existing_user_columns:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_definition}")
                print(f"Added '{col_name}' column to 'users' table.")
            except sqlite3.OperationalError as e:
                print(f"Could not add column '{col_name}' to 'users' table: {e}. It might already exist or there's a schema issue.")

    # --- Auth Tokens Table ---
    # Stores various authentication tokens (e.g., session tokens, password reset tokens).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS auth_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,                   -- Foreign key to the users table
        token_hash TEXT UNIQUE NOT NULL,            -- Hashed value of the token
        token_type TEXT NOT NULL CHECK(token_type IN ('magic_login', 'session', 'password_reset')), -- Type of token
        expires_at TIMESTAMP NOT NULL,              -- Expiration timestamp for the token
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When the token was created
        used_at TIMESTAMP,                          -- When the token was used (if applicable, e.g., for one-time tokens)
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE -- If a user is deleted, their tokens are also deleted
    );
    """)
    print("Ensured 'auth_tokens' table exists.")

    # --- Sessions Table ---
    # Represents individual chat sessions.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,                        -- Unique ID for the session (e.g., UUID)
        host_user_id INTEGER NOT NULL,              -- User who created/hosts the session
        name TEXT,                                  -- User-defined or auto-generated name for the session
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When the session was created
        last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When the session was last accessed or had activity
        is_active BOOLEAN DEFAULT TRUE NOT NULL,    -- Whether the session is currently active or soft-deleted
        FOREIGN KEY (host_user_id) REFERENCES users (id) ON DELETE CASCADE -- If host user is deleted, their sessions are deleted
    );
    """)
    print("Ensured 'sessions' table exists.")

    # --- Session Participants Table ---
    # Manages which users are part of which sessions (for multi-user scenarios, or just the host).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_participants (
        session_id TEXT NOT NULL,                   -- Foreign key to the sessions table
        user_id INTEGER NOT NULL,                   -- Foreign key to the users table
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When the user joined the session
        PRIMARY KEY (session_id, user_id),          -- Composite primary key
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE, -- If session is deleted, participation records are deleted
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE      -- If user is deleted, their participation records are deleted
    );
    """)
    print("Ensured 'session_participants' table exists.")

    # --- Chat Messages Table ---
    # Stores all messages within chat sessions.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,                   -- Foreign key to the sessions table
        user_id INTEGER,                            -- Foreign key to users table (NULL if AI/system message)
        sender_name TEXT,                           -- Display name of the sender (e.g., user's name, "AI")
        sender_type TEXT NOT NULL CHECK(sender_type IN ('user', 'ai', 'system', 'anon_user')), -- Type of sender
        content TEXT NOT NULL,                      -- The actual message content
        client_id_temp TEXT,                        -- Optional temporary ID from client for optimistic UI updates
        turn_id INTEGER,                            -- Groups related user and AI messages in a conversational turn
        thinking_content TEXT,                      -- Stores AI's "thinking" process or intermediate thoughts
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When the message was recorded
        model_provider_id TEXT,                     -- For AI messages: which LLM provider was used
        model_id TEXT,                              -- For AI messages: which specific LLM model was used
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE, -- If session is deleted, its messages are deleted
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL    -- If user is deleted, messages remain but user_id becomes NULL
    );
    """)
    print("Ensured 'chat_messages' table exists.")

    # Add new columns to 'chat_messages' if they don't exist
    chat_messages_columns_to_add = {
        "model_provider_id": "TEXT",
        "model_id": "TEXT"
    }
    cursor.execute("PRAGMA table_info(chat_messages)")
    existing_chat_columns = [col[1] for col in cursor.fetchall()]

    for col_name, col_type in chat_messages_columns_to_add.items():
        if col_name not in existing_chat_columns:
            try:
                cursor.execute(f"ALTER TABLE chat_messages ADD COLUMN {col_name} {col_type}")
                print(f"Added '{col_name}' column to 'chat_messages' table.")
            except sqlite3.OperationalError as e:
                print(f"Could not add column '{col_name}' to 'chat_messages' table: {e}. It might already exist.")

    # --- Session Memory State Table ---
    # Stores serialized conversation memory for LLMs, allowing sessions to be resumed with context.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_memory_state (
        session_id TEXT PRIMARY KEY,                -- Foreign key to the sessions table
        memory_state_json TEXT NOT NULL,            -- Serialized memory state (e.g., JSON string)
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- When this memory state was last updated
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE -- If session is deleted, its memory state is deleted
    );
    """)
    print("Ensured 'session_memory_state' table exists.")

    # --- Password Reset Attempts Table ---
    # Logs attempts to reset passwords, useful for rate limiting and security auditing.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,                        -- Email for which the reset was attempted
        ip_address TEXT,                            -- Optional: IP address of the requester
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- Timestamp of the reset attempt
    );
    """)
    print("Ensured 'password_reset_attempts' table exists.")


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS code_execution_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,                   
        code_block_id TEXT NOT NULL,               
        language TEXT NOT NULL,                     
        code_content TEXT NOT NULL,                 
        output_content TEXT,                        
        html_content TEXT,                          
        exit_code INTEGER,                          
        error_message TEXT,                         
        execution_status TEXT NOT NULL DEFAULT 'completed' CHECK(execution_status IN ('completed', 'error', 'timeout')), 
        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
        turn_id INTEGER,                            
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE 
    );
    """)
    print("Ensured 'code_execution_results' table exists.")



    # --- Indexes for Performance ---
    # These indexes help speed up common queries.
    print("Ensuring database indexes exist...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_token_hash ON auth_tokens (token_hash);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_id ON auth_tokens (user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_host_user_id ON sessions (host_user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON session_participants (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_user_id ON session_participants (user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_timestamp ON chat_messages (timestamp);") # For ordering messages
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_memory_state_session_id ON session_memory_state (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_attempts_email_time ON password_reset_attempts (email, attempted_at);")
    print("Ensured all indexes exist.")

    conn.commit() # Save all schema changes
    conn.close()
    print("Database initialization process complete.")

# --- Utility Functions ---

def hash_value(value: str) -> str:
    """
    Hashes a string value (e.g., for storing tokens securely) using SHA256.
    """
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def generate_secure_token(length: int = 32) -> str:
    """
    Generates a cryptographically secure, URL-safe text string.
    Useful for session tokens, password reset tokens, etc.
    """
    return secrets.token_urlsafe(length)

# This allows the script to be run directly to initialize the database
# e.g., `python -m app.database` from the project root directory.
if __name__ == "__main__":
    print(f"Running database script directly. Current Working Directory: {Path.cwd()}")
    print(f"Project Root (expected): {PROJECT_ROOT}")
    print(f"Database will be created/checked at: {DATABASE_PATH}")
    
    # Ensure the parent directory for the database file exists before trying to create/connect to it
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    init_db()