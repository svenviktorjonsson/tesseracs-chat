import sqlite3
import os
from pathlib import Path
import hashlib
import secrets
import datetime

APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent
DATABASE_NAME = "tesseracs_chat.db"
DATABASE_PATH = PROJECT_ROOT / DATABASE_NAME

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

def get_edited_code_blocks(session_id: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    edited_blocks = {}
    try:
        cursor.execute(
            "SELECT code_block_id, edited_content FROM edited_code_blocks WHERE session_id = ?",
            (session_id,)
        )
        rows = cursor.fetchall()
        for row in rows:
            edited_blocks[row['code_block_id']] = row['edited_content']
        return edited_blocks
    except Exception as e:
        print(f"Error retrieving edited code blocks for session {session_id}: {e}")
        return {}
    finally:
        conn.close()

def delete_edited_code_block(session_id, code_block_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM edited_code_blocks WHERE session_id = ? AND code_block_id = ?",
            (session_id, code_block_id)
        )
        conn.commit()
        print(f"Successfully deleted edited content for {code_block_id} in session {session_id}")
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error deleting edited code block for {code_block_id}: {e}")
        return False
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
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    print(f"Initializing database schema at {DATABASE_PATH}...")
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE NOT NULL,
        is_bot BOOLEAN DEFAULT FALSE NOT NULL,
        selected_llm_provider_id TEXT,
        selected_llm_model_id TEXT,
        user_llm_api_key_encrypted TEXT,
        selected_llm_base_url TEXT
    );
    """)
    print("Ensured 'users' table exists.")

    user_table_columns_to_add = {
        "is_bot": "BOOLEAN DEFAULT FALSE NOT NULL"
    }
    cursor.execute("PRAGMA table_info(users)")
    existing_user_columns = [col[1] for col in cursor.fetchall()]
    for col_name, col_definition in user_table_columns_to_add.items():
        if col_name not in existing_user_columns:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_definition}")
                print(f"Added '{col_name}' column to 'users' table.")
            except sqlite3.OperationalError as e:
                print(f"Could not add column '{col_name}' to 'users' table: {e}.")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        host_user_id INTEGER NOT NULL,
        name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE NOT NULL,
        access_level TEXT NOT NULL DEFAULT 'private',
        passcode_hash TEXT,
        FOREIGN KEY (host_user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'sessions' table exists.")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_participants (
        session_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_hidden BOOLEAN DEFAULT FALSE NOT NULL,
        PRIMARY KEY (session_id, user_id),
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'session_participants' table exists.")

    cursor.execute("PRAGMA table_info(session_participants)")
    existing_participant_columns = [col[1] for col in cursor.fetchall()]
    if "is_hidden" not in existing_participant_columns:
        try:
            cursor.execute("ALTER TABLE session_participants ADD COLUMN is_hidden BOOLEAN DEFAULT FALSE NOT NULL")
            print("Added 'is_hidden' column to 'session_participants' table.")
        except sqlite3.OperationalError as e:
            print(f"Could not add 'is_hidden' column to 'session_participants': {e}.")

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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        user_id INTEGER,
        sender_name TEXT,
        sender_type TEXT NOT NULL CHECK(sender_type IN ('user', 'ai', 'system', 'anon_user')),
        content TEXT NOT NULL,
        client_id_temp TEXT,
        turn_id INTEGER,
        thinking_content TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        model_provider_id TEXT,
        model_id TEXT,
        reply_to_message_id INTEGER,
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
        FOREIGN KEY (reply_to_message_id) REFERENCES chat_messages (id) ON DELETE SET NULL
    );
    """)
    print("Ensured 'chat_messages' table exists.")

    chat_messages_columns_to_add = {
        "reply_to_message_id": "INTEGER"
    }
    cursor.execute("PRAGMA table_info(chat_messages)")
    existing_chat_columns = [col[1] for col in cursor.fetchall()]
    for col_name, col_type in chat_messages_columns_to_add.items():
        if col_name not in existing_chat_columns:
            try:
                cursor.execute(f"ALTER TABLE chat_messages ADD COLUMN {col_name} {col_type}")
                print(f"Added '{col_name}' column to 'chat_messages' table.")
            except sqlite3.OperationalError as e:
                print(f"Could not add column '{col_name}' to 'chat_messages' table: {e}.")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_bots (
        session_id TEXT NOT NULL,
        bot_user_id INTEGER NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (session_id, bot_user_id),
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
        FOREIGN KEY (bot_user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'session_bots' table exists.")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS edited_code_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        code_block_id TEXT NOT NULL,
        language TEXT NOT NULL,
        edited_content TEXT NOT NULL,
        edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(session_id, code_block_id),
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'edited_code_blocks' table exists.")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_memory_state (
        session_id TEXT PRIMARY KEY,
        memory_state_json TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
    );
    """)
    print("Ensured 'session_memory_state' table exists.")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        ip_address TEXT,
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    print("Ensuring database indexes exist...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_token_hash ON auth_tokens (token_hash);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_id ON auth_tokens (user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_host_user_id ON sessions (host_user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON session_participants (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_user_id ON session_participants (user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_timestamp ON chat_messages (timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_memory_state_session_id ON session_memory_state (session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_attempts_email_time ON password_reset_attempts (email, attempted_at);")
    print("Ensured all indexes exist.")

    conn.commit()
    conn.close()
    print("Database initialization process complete.")

def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def generate_secure_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)

def save_edited_code_content(session_id, code_block_id, language, code_content):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO edited_code_blocks 
            (session_id, code_block_id, language, edited_content, edited_at)
            VALUES (?, ?, ?, ?, datetime('now', 'utc'))
        """, (session_id, code_block_id, language, code_content))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error saving edited code content for {code_block_id}: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print(f"Running database script directly. Current Working Directory: {Path.cwd()}")
    print(f"Project Root (expected): {PROJECT_ROOT}")
    print(f"Database will be created/checked at: {DATABASE_PATH}")
    
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    init_db()

