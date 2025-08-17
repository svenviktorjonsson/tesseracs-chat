import sqlite3
from pathlib import Path

# Assuming the database is in the project root
DATABASE_PATH = Path("tesseracs_chat.db")

def check_database():
    if not DATABASE_PATH.exists():
        print(f"Database not found at {DATABASE_PATH}")
        return
    
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=== Recent AI Messages ===")
    cursor.execute("""
        SELECT id, turn_id, session_id, content, timestamp 
        FROM chat_messages 
        WHERE sender_type = 'ai' 
        ORDER BY id DESC 
        LIMIT 5
    """)
    
    ai_messages = cursor.fetchall()
    for msg in ai_messages:
        print(f"ID: {msg['id']}, Turn ID: {msg['turn_id']}, Session: {msg['session_id'][:8]}...")
        print(f"Content preview: {msg['content'][:50]}...")
        print(f"Timestamp: {msg['timestamp']}")
        print("-" * 50)
    
    print("\n=== Code Execution Results ===")
    cursor.execute("""
        SELECT code_block_id, session_id, language, exit_code, executed_at
        FROM code_execution_results 
        ORDER BY executed_at DESC 
        LIMIT 5
    """)
    
    code_results = cursor.fetchall()
    for result in code_results:
        print(f"Block ID: {result['code_block_id']}")
        print(f"Session: {result['session_id'][:8]}...")
        print(f"Language: {result['language']}, Exit Code: {result['exit_code']}")
        print(f"Executed: {result['executed_at']}")
        print("-" * 50)
    
    print("\n=== Checking Turn ID vs Code Block ID Matches ===")
    cursor.execute("""
        SELECT cm.id, cm.turn_id, cm.session_id, cer.code_block_id
        FROM chat_messages cm
        LEFT JOIN code_execution_results cer ON cm.session_id = cer.session_id
        WHERE cm.sender_type = 'ai' AND cer.code_block_id IS NOT NULL
        ORDER BY cm.id DESC
        LIMIT 5
    """)
    
    matches = cursor.fetchall()
    for match in matches:
        expected_block_id = f"code-block-turn{match['turn_id']}-1"
        actual_block_id = match['code_block_id']
        matches_expected = expected_block_id == actual_block_id
        
        print(f"Message ID: {match['id']}, Turn ID: {match['turn_id']}")
        print(f"Expected Block ID: {expected_block_id}")
        print(f"Actual Block ID: {actual_block_id}")
        print(f"Match: {matches_expected}")
        print("-" * 50)
    
    conn.close()

if __name__ == "__main__":
    check_database()