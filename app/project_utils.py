# app/project_utils.py
import re
from typing import Optional, Dict, List, Any

def parse_project_from_content(content: str) -> Optional[Dict[str, Any]]:
    """
    Parses a multi-file project from a string based on a specific format.
    
    Format Example:
    \\project{name: "Project Name", run: "run command"}
    ./path/to/file.py:
    ```python
    ...
    ```
    \\endproject
    """
    project_match = re.search(r"\\project\{name: \"(.*?)\", run: \"(.*?)\"\}", content, re.DOTALL)
    end_project_match = re.search(r"\\endproject", content)

    if not project_match or not end_project_match:
        return None

    project_name = project_match.group(1)
    run_command = project_match.group(2)
    
    project_content = content[project_match.end():end_project_match.start()]

    file_regex = re.compile(
        r"^\s*(?P<path>[\w\./\-\_]+):\s*\n"  # File path line
        r"```(?P<language>\w*)\n"             # Opening fence with language
        r"(?P<content>[\s\S]*?)\n"            # File content
        r"```",                               # Closing fence
        re.MULTILINE
    )
    
    files = []
    for match in file_regex.finditer(project_content):
        files.append({
            "path": match.group("path").strip(),
            "language": match.group("language").strip(),
            "content": match.group("content")
        })

    if not files:
        return None

    return {
        "name": project_name,
        "run_command": run_command,
        "files": files
    }