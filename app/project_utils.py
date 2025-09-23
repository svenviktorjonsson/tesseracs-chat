# app/project_utils.py

import re
import os
import tempfile
import subprocess
import json
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
import uuid

def get_language_from_extension(file_path: str) -> str:
    """Deduces the programming language from a file extension."""
    extension = Path(file_path).suffix.lower().strip('.')
    lang_map = {
        'py': 'python', 'js': 'javascript', 'html': 'html', 'css': 'css',
        'sh': 'bash', 'json': 'json', 'c': 'c', 'cpp': 'cpp',
        'cs': 'csharp', 'go': 'go', 'rs': 'rust', 'ts': 'typescript',
        'java': 'java', 'md': 'markdown', 'yaml': 'yaml', 'yml': 'yaml',
        'txt': 'plaintext'
    }
    return lang_map.get(extension, 'plaintext')

def parse_file_blocks(content: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parses all _FILE_START_..._FILE_END_ blocks from a string and returns a list of file dictionaries.
    """
    try:
        file_regex = re.compile(r"_FILE_START_(.*?)_FILE_END_", re.DOTALL)
        
        files = []
        for file_match in file_regex.finditer(content):
            file_block_content = file_match.group(1).strip()
            
            file_args_match = re.search(r"^(.*?)\s*_JSON_END_", file_block_content, re.DOTALL)
            if not file_args_match:
                continue

            try:
                file_args_json = file_args_match.group(1).strip()
                if not file_args_json.startswith('{'):
                    continue
                file_args = json.loads(file_args_json)
                path = file_args.get("path")
                if not path:
                    continue
            except json.JSONDecodeError:
                continue

            file_content_str = file_block_content[file_args_match.end():].strip()
            
            files.append({
                "path": path,
                "language": get_language_from_extension(path),
                "content": file_content_str
            })

        return files if files else None
    except Exception as e:
        print(f"PROJECT_UTILS PARSE ERROR: Failed to parse file blocks - {e}")
        return None

def create_project_directory_and_files(project_data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Creates a temporary directory and saves project files.
    Returns a tuple of (path, error_message). On success, error_message is None.
    """
    project_dir = None
    try:
        project_dir = tempfile.mkdtemp(prefix="tesseracs_proj_")
        
        for file_info in project_data.get("files", []):
            file_path_str = file_info.get("path")
            content = file_info.get("content", "")

            if not file_path_str or file_path_str.startswith("/") or ".." in file_path_str:
                continue

            full_path = Path(project_dir) / file_path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8", newline='\n')

        subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "AI Assistant"], cwd=project_dir, check=True)
        subprocess.run(["git", "config", "user.email", "ai@tesseracs.com"], cwd=project_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=project_dir, check=True)
        commit_message = f"Initial commit: Create project '{project_data.get('name', 'Untitled Project')}'"
        subprocess.run(["git", "commit", "-m", commit_message], cwd=project_dir, check=True, capture_output=True, text=True)
        
        return project_dir, None

    except FileNotFoundError as e:
        error_msg = f"Command '{e.filename}' not found. Ensure git is installed in the Docker image."
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        return None, error_msg
    except subprocess.CalledProcessError as e:
        error_msg = f"A git command failed:\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}"
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        return None, error_msg
    except (OSError, Exception) as e:
        error_msg = f"Failed to create project directory or files: {e}"
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        return None, error_msg