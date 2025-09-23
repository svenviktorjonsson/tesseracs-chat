import sqlite3
import sys
import os
import shutil
from pathlib import Path

def migrate_database():
    """
    Updates the database to the new schema, preserving all existing user
    and message data.
    """
    try:
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'app')))
        from app import config
        DATABASE_PATH = config.DATABASE_PATH
        print(f"Targeting database at: {DATABASE_PATH}")
    except (ImportError, AttributeError):
        print("Could not import config. Assuming default database path.")
        PROJECT_ROOT = Path(__file__).resolve().parent
        DATABASE_PATH = PROJECT_ROOT / "tesseracs_chat.db"

    if not DATABASE_PATH.exists():
        print(f"Error: Database file not found at {DATABASE_PATH}. Please run the app first to create it.")
        return

    # --- Step 1: Create a backup ---
    backup_path = DATABASE_PATH.with_suffix('.db.bak')
    print(f"Creating a backup of the database at: {backup_path}")
    try:
        shutil.copy(DATABASE_PATH, backup_path)
        print("Backup created successfully.")
    except Exception as e:
        print(f"Could not create backup: {e}. Aborting migration.")
        return

    # --- Step 2: Confirmation ---
    confirm = input("This script will alter the database schema to support structured AI messages. A backup has been created. Continue? [y/N]: ")
    if confirm.lower() != 'y':
        print("Migration cancelled by user.")
        return

    # --- Step 3: Migration ---
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        print("\nStarting database migration...")
        
        cursor.execute("PRAGMA foreign_keys=off;")
        conn.execute("BEGIN TRANSACTION;")

        # Check existing columns to make the script safely re-runnable
        cursor.execute("PRAGMA table_info(chat_messages)")
        chat_columns = [row[1] for row in cursor.fetchall()]
        
        # --- Migrate chat_messages table ---
        if 'prompting_user_id' not in chat_columns:
            print("Migrating 'chat_messages' table...")
            cursor.execute("ALTER TABLE chat_messages RENAME TO chat_messages_old;")
            
            cursor.execute("""
            CREATE TABLE chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id INTEGER,
                sender_name TEXT,
                sender_type TEXT NOT NULL CHECK(sender_type IN ('user', 'ai', 'system')),
                content TEXT,
                turn_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reply_to_message_id INTEGER,
                prompting_user_id INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
                FOREIGN KEY (reply_to_message_id) REFERENCES chat_messages (id) ON DELETE SET NULL,
                FOREIGN KEY (prompting_user_id) REFERENCES users (id) ON DELETE SET NULL
            );
            """)

            # Copy data from the old table to the new one
            cursor.execute("""
            INSERT INTO chat_messages (id, session_id, user_id, sender_name, sender_type, content, turn_id, timestamp, reply_to_message_id)
            SELECT id, session_id, user_id, sender_name, sender_type, content, turn_id, timestamp, reply_to_message_id
            FROM chat_messages_old;
            """)

            cursor.execute("DROP TABLE chat_messages_old;")
            print("'chat_messages' table migrated successfully.")
        else:
            print("'chat_messages' table already has the new schema. Skipping.")

        # --- Create message_files table ---
        print("Ensuring 'message_files' table exists...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS message_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            content TEXT NOT NULL,
            language TEXT,
            FOREIGN KEY (message_id) REFERENCES chat_messages (id) ON DELETE CASCADE
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_message_files_message_id ON message_files (message_id);")
        print("'message_files' table is ready.")

        conn.commit()
        print("\nMigration complete and changes committed.")

    except sqlite3.Error as e:
        print(f"\nAn error occurred: {e}")
        if conn:
            conn.rollback()
            print("Transaction rolled back. Your original database is safe.")
    finally:
        if conn:
            cursor.execute("PRAGMA foreign_keys=on;")
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    migrate_database()