import time
import os
import tempfile
from pathlib import Path

# --- Configuration ---
# Get the system's temporary directory (e.g., '/tmp' on Linux)
TEMP_DIR_BASE = Path(tempfile.gettempdir())
# This is the prefix used by your application to create temporary project folders
DIR_PREFIX = "tesseracs_proj_"

def watch_directory():
    """
    Monitors the system's temp directory for project folders, reporting on their
    creation, contents, and lifespan.
    """
    print(f"--- Starting Watcher ---")
    print(f"[*] Watching for directories starting with '{DIR_PREFIX}' in: {TEMP_DIR_BASE}")
    print("... (Press Ctrl+C to stop) ...\n")

    # A dictionary to keep track of the directories we are currently monitoring
    # Format: { 'path/to/dir': start_time }
    watched_dirs = {}

    try:
        while True:
            # Find all directories that currently match our pattern
            current_dirs = {p for p in TEMP_DIR_BASE.glob(f"{DIR_PREFIX}*") if p.is_dir()}
            
            # --- Check for NEW directories ---
            for path in current_dirs:
                if path not in watched_dirs:
                    start_time = time.time()
                    watched_dirs[path] = start_time
                    
                    print(f"--- [+] Directory FOUND! ---")
                    print(f"  [Time]     : {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"  [Full Path]: {path}")
                    
                    try:
                        files = [f.name for f in path.iterdir()]
                        if files:
                            print(f"  [Files]    : {', '.join(files)}")
                        else:
                            print("  [Files]    : (Directory is empty)")
                    except OSError as e:
                        print(f"  [Files]    : Could not list files. Error: {e}")
                    print("-" * 28 + "\n")


            # --- Check for DELETED directories ---
            # Find which of our watched directories no longer exist
            deleted_dirs = set(watched_dirs.keys()) - current_dirs
            for path in deleted_dirs:
                start_time = watched_dirs.pop(path) # Remove from watch list
                end_time = time.time()
                duration = end_time - start_time
                
                print(f"--- [-] Directory GONE! ---")
                print(f"  [Time]     : {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  [Full Path]: {path}")
                print(f"  [Existed For]: {duration:.4f} seconds")
                print("-" * 28 + "\n")

            # Wait a fraction of a second before checking again to avoid high CPU usage
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n--- Watcher Stopped ---")

if __name__ == "__main__":
    watch_directory()
