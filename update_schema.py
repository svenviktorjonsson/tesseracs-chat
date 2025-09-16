import sqlite3
from pathlib import Path
import sys
import os

# Ensure the app directory is in the Python path to import config
# This allows the script to be run from the project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'app')))

def update_database_schema():
    """
    Applies necessary schema updates to the SQLite database.
    This script is idempotent and can be run multiple times safely.
    """
    try:
        from app import config
        DATABASE_PATH = config.DATABASE_PATH
    except (ImportError, AttributeError):
        print("Could not import config. Assuming default database path.")
        PROJECT_ROOT = Path(__file__).resolve().parent
        DATABASE_PATH = PROJECT_ROOT / "tesseracs_chat.db"

    print(f"Connecting to database at: {DATABASE_PATH}")
    if not DATABASE_PATH.exists():
        print(f"Error: Database file not found at {DATABASE_PATH}. Please run the main application first to create it.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        print("\n--- Starting Schema Update ---")

        # Helper to check for existing columns
        def get_existing_columns(table_name):
            try:
                cursor.execute(f"PRAGMA table_info({table_name})")
                return [row['name'] for row in cursor.fetchall()]
            except sqlite3.OperationalError:
                return [] # Table might not exist yet, e.g., during creation

        # 1. Update 'users' table: Add 'is_bot' column
        print("\nChecking 'users' table...")
        user_columns = get_existing_columns('users')
        if 'is_bot' not in user_columns:
            print("-> Adding 'is_bot' column to 'users' table.")
            cursor.execute("ALTER TABLE users ADD COLUMN is_bot BOOLEAN DEFAULT FALSE NOT NULL")
            print("-> 'is_bot' column added successfully.")
        else:
            print("-> 'is_bot' column already exists. Skipping.")

        # 2. Update 'sessions' table: Replace 'is_public' with 'access_level' and add 'passcode_hash'
        print("\nChecking 'sessions' table...")
        session_columns = get_existing_columns('sessions')
        if 'access_level' not in session_columns:
            print("-> Migrating 'sessions' table to new schema (adding access_level, passcode_hash)...")
            cursor.execute("ALTER TABLE sessions RENAME TO sessions_old")
            print("   - Renamed existing table to 'sessions_old'.")

            cursor.execute("""
            CREATE TABLE sessions (
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
            print("   - Created new 'sessions' table with updated schema.")

            # Check if the old table had an 'is_public' column to migrate from
            old_session_columns = get_existing_columns('sessions_old')
            if 'is_public' in old_session_columns:
                print("   - 'is_public' column found in old table. Migrating values to 'access_level'.")
                cursor.execute("""
                INSERT INTO sessions (id, host_user_id, name, created_at, last_accessed_at, is_active, access_level)
                SELECT id, host_user_id, name, created_at, last_accessed_at, is_active,
                       CASE WHEN is_public = 1 THEN 'public' ELSE 'private' END
                FROM sessions_old;
                """)
            else:
                print("   - 'is_public' column not found. Copying data and defaulting 'access_level' to 'private'.")
                cursor.execute("""
                INSERT INTO sessions (id, host_user_id, name, created_at, last_accessed_at, is_active)
                SELECT id, host_user_id, name, created_at, last_accessed_at, is_active
                FROM sessions_old;
                """)
            
            print("   - Copied data from 'sessions_old' to new 'sessions' table.")

            cursor.execute("DROP TABLE sessions_old")
            print("   - Dropped 'sessions_old' table.")
            print("-> 'sessions' table migrated successfully.")
        else:
            print("-> 'sessions' table already has the new schema. Skipping.")

        # 3. Create 'session_bots' table if it doesn't exist
        print("\nChecking 'session_bots' table...")
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
        print("-> Ensured 'session_bots' table exists.")

        conn.commit()
        print("\n--- Schema Update Complete ---")

        # 4. Update 'chat_messages' table: Add 'project_id' column
        print("\nChecking 'chat_messages' table...")
        chat_columns = get_existing_columns('chat_messages')
        if 'project_id' not in chat_columns:
            print("-> Adding 'project_id' column to 'chat_messages' table.")
            cursor.execute("ALTER TABLE chat_messages ADD COLUMN project_id TEXT")
            print("-> 'project_id' column added successfully.")
        else:
            print("-> 'project_id' column already exists. Skipping.")
        
        conn.commit()

    except sqlite3.Error as e:
        print(f"\nAn error occurred: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    update_database_schema()

