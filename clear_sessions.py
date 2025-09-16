import sqlite3
import sys
import os
from pathlib import Path

def clear_all_sessions():
    """
    Connects to the database and deletes all rows from the 'sessions' table.
    Due to 'ON DELETE CASCADE' constraints, this will also clear all related
    chat messages, participants, code results, etc., while leaving the 'users'
    table and other user-specific data intact.
    """
    try:
        # Find the database file (consistent with the main app)
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'app')))
        from app import config
        DATABASE_PATH = config.DATABASE_PATH
        print(f"Targeting database at: {DATABASE_PATH}")
    except (ImportError, AttributeError):
        print("Could not import config. Assuming default database path in project root.")
        PROJECT_ROOT = Path(__file__).resolve().parent
        DATABASE_PATH = PROJECT_ROOT / "tesseracs_chat.db"

    if not DATABASE_PATH.exists():
        print(f"Error: Database file not found at {DATABASE_PATH}. Nothing to clear.")
        return

    # Confirmation Prompt for safety
    confirm = input("Are you sure you want to permanently delete ALL chat sessions, messages, and related data? User accounts will NOT be affected. [y/N]: ")
    if confirm.lower() != 'y':
        print("Operation cancelled.")
        return

    # --- Database Operation ---
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        # Enable foreign key support is crucial for cascading deletes to work
        cursor.execute("PRAGMA foreign_keys = ON;")
        print("Foreign key support enabled.")

        # Delete all sessions. The database will handle the cascade.
        print("Deleting all rows from 'sessions' table...")
        cursor.execute("DELETE FROM sessions;")
        print(f"{cursor.rowcount} sessions and all related data have been deleted.")

        # Reclaim the freed-up disk space
        print("Reclaiming disk space (VACUUM)...")
        cursor.execute("VACUUM;")
        
        conn.commit()
        print("\nOperation complete. All chat sessions have been cleared.")

    except sqlite3.Error as e:
        print(f"\nAn error occurred: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    clear_all_sessions()