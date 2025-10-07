import re
import os
import tempfile
import subprocess
import json
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
import uuid
import tarfile
import io
import shutil
import zipfile

def get_language_from_extension(file_path: str) -> str:
    """
    Deduces a standardized programming language name from a file's extension.
    """
    extension = Path(file_path).suffix.lower().strip('.')
    lang_map = {
        'py': 'python', 'js': 'javascript', 'html': 'html', 'css': 'css',
        'sh': 'bash', 'json': 'json', 'c': 'c', 'cpp': 'cpp',
        'cs': 'csharp', 'go': 'go', 'rs': 'rust', 'ts': 'typescript',
        'java': 'java', 'md': 'markdown', 'yaml': 'yaml', 'yml': 'yaml',
        'txt': 'plaintext', 'tex': 'latex'
    }
    return lang_map.get(extension, 'plaintext')

def parse_file_blocks(content: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parses a string from an AI's response to extract structured file blocks.
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

def create_zip_from_blob(repo_blob: bytes) -> Tuple[Optional[io.BytesIO], Optional[str]]:
    """
    Takes a gzipped tarball blob of a git repo, unpacks it to a temporary directory,
    creates a zip archive of its contents (excluding .git and metadata), and returns the zip as an in-memory buffer.
    """
    temp_dir_to_clean = None
    try:
        # Unpack the repository into a temporary directory. This returns the path to the project's root.
        project_root, error = unpack_git_repo_to_temp_dir(repo_blob)
        if error or not project_root:
            raise Exception(f"Failed to unpack repo: {error}")

        # Keep track of the parent temp directory for cleanup
        temp_dir_to_clean = os.path.dirname(project_root)
        
        # Create an in-memory binary buffer to hold the zip file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Walk through the unpacked project directory
            for root, _, files in os.walk(project_root):
                # Skip the .git directory entirely
                if '.git' in root:
                    continue 

                for file in files:
                    # Skip our internal metadata file
                    if file == '.tesseracs_meta.json':
                        continue
                    
                    file_path = os.path.join(root, file)
                    # `arcname` determines the path inside the zip file
                    arcname = os.path.relpath(file_path, project_root)
                    zipf.write(file_path, arcname)

        # Rewind the buffer to the beginning so it can be read
        zip_buffer.seek(0)
        return zip_buffer, None
    except Exception as e:
        return None, str(e)
    finally:
        # IMPORTANT: Clean up the temporary directory after we're done
        if temp_dir_to_clean and os.path.exists(temp_dir_to_clean):
            shutil.rmtree(temp_dir_to_clean)

def add_file_and_repack_repo(repo_blob: bytes, upload_data, user_name: str) -> Tuple[Optional[bytes], Optional[str]]:
    temp_dir_to_clean = None
    try:
        project_root, error = unpack_git_repo_to_temp_dir(repo_blob)
        if error or not project_root:
            raise Exception(f"Failed to unpack repo for update: {error}")
        
        temp_dir_to_clean = os.path.dirname(project_root)

        target_dir = os.path.join(project_root, upload_data.path.lstrip('./'))
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, upload_data.filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(upload_data.content)
            
        subprocess.run(["git", "config", "user.name", user_name], cwd=project_root, check=True)
        subprocess.run(["git", "config", "user.email", f"{user_name.replace(' ', '.')}@tesseracs.dev"], cwd=project_root, check=True)
        
        subprocess.run(["git", "add", "."], cwd=project_root, check=True, capture_output=True)
        commit_message = f"User upload: Add {upload_data.filename} by {user_name}"
        subprocess.run(["git", "commit", "--allow-empty", "-m", commit_message], cwd=project_root, check=True, capture_output=True)
        
        in_memory_file = io.BytesIO()
        with tarfile.open(fileobj=in_memory_file, mode="w:gz") as tar:
            tar.add(project_root, arcname=os.path.basename(project_root))
        
        return in_memory_file.getvalue(), None

    except Exception as e:
        return None, str(e)
    finally:
        if temp_dir_to_clean and os.path.exists(temp_dir_to_clean):
            shutil.rmtree(temp_dir_to_clean)

def move_file_and_repack_repo(repo_blob: bytes, source_path: str, destination_path: str, user_name: str) -> Tuple[Optional[bytes], Optional[str]]:
    temp_dir_to_clean = None
    try:
        project_root, error = unpack_git_repo_to_temp_dir(repo_blob)
        if error or not project_root:
            raise Exception(f"Failed to unpack repo for move: {error}")
        
        temp_dir_to_clean = os.path.dirname(project_root)

        source = source_path.lstrip('./')
        destination = destination_path.lstrip('./')
        
        subprocess.run(["git", "config", "user.name", user_name], cwd=project_root, check=True)
        subprocess.run(["git", "config", "user.email", f"{user_name.replace(' ', '.')}@tesseracs.dev"], cwd=project_root, check=True)

        subprocess.run(
            ["git", "mv", source, destination], 
            cwd=project_root, 
            check=True, 
            capture_output=True, 
            text=True
        )

        commit_message = f"User move: {source} to {destination} by {user_name}"
        subprocess.run(
            ["git", "commit", "-m", commit_message], 
            cwd=project_root, 
            check=True, 
            capture_output=True, 
            text=True
        )
        
        in_memory_file = io.BytesIO()
        with tarfile.open(fileobj=in_memory_file, mode="w:gz") as tar:
            tar.add(project_root, arcname=os.path.basename(project_root))
        
        return in_memory_file.getvalue(), None

    except subprocess.CalledProcessError as e:
        error_msg = f"Git move operation failed: {e.stderr}"
        print(f"PROJECT_UTILS GIT ERROR: {error_msg}")
        return None, error_msg
    except Exception as e:
        return None, str(e)
    finally:
        if temp_dir_to_clean and os.path.exists(temp_dir_to_clean):
            shutil.rmtree(temp_dir_to_clean)

def unpack_git_repo_to_temp_dir(repo_blob: bytes) -> Tuple[Optional[str], Optional[str]]:
    unpack_dir = None
    try:
        in_memory_file = io.BytesIO(repo_blob)
        unpack_dir = tempfile.mkdtemp(prefix="tesseracs_proj_unpacked_")
        
        with tarfile.open(fileobj=in_memory_file, mode="r:gz") as tar:
            def is_within_directory(directory, target):
                abs_directory = os.path.abspath(directory)
                abs_target = os.path.abspath(target)
                prefix = os.path.commonprefix([abs_directory, abs_target])
                return prefix == abs_directory
            
            for member in tar.getmembers():
                member_path = os.path.join(unpack_dir, member.name)
                if not is_within_directory(unpack_dir, member_path):
                    raise Exception("Attempted Path Traversal in Tar File")
            
            tar.extractall(path=unpack_dir)

        unpacked_contents = os.listdir(unpack_dir)
        if len(unpacked_contents) == 1 and os.path.isdir(os.path.join(unpack_dir, unpacked_contents[0])):
            project_root_path = os.path.join(unpack_dir, unpacked_contents[0])
            return project_root_path, None
        else:
            return unpack_dir, None
            
    except Exception as e:
        error_msg = f"Failed to unpack git repo to temp dir: {e}"
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        if unpack_dir and os.path.exists(unpack_dir):
            shutil.rmtree(unpack_dir)
        return None, error_msg

def create_project_directory_and_files(project_data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Creates a temporary directory inside the SHARED './.data/projects' folder
    and writes all project files into it.
    """
    project_dir = None
    try:
        # This path now EXACTLY matches the path from docker-compose.yml
        base_dir = Path("./.data/projects")
        base_dir.mkdir(parents=True, exist_ok=True)
        
        project_dir_name = f"tesseracs_proj_{uuid.uuid4()}"
        project_dir = base_dir / project_dir_name
        project_dir.mkdir()
        
        print(f"DOCKER_UTILS: Created shared project directory at: {project_dir}")
        
        files_to_create = project_data.get("files")
        if not files_to_create or not isinstance(files_to_create, list):
            return None, "Project data is missing or does not contain a valid list of files."
            
        for file_info in files_to_create:
            file_path_str = file_info.get("path")
            content = file_info.get("content", "")
            
            if not file_path_str or file_path_str.startswith("/") or ".." in file_path_str:
                print(f"DOCKER_UTILS: Skipping potentially unsafe file path: {file_path_str}")
                continue
                
            full_path = project_dir / file_path_str.lstrip('./')
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8", newline='\n')
            
            if file_path_str.endswith('run.sh'):
                os.chmod(full_path, 0o755)
                
            print(f"DOCKER_UTILS: Successfully wrote file '{file_path_str}' to directory.")
            
        print(f"DOCKER_UTILS: Wrote a total of {len(files_to_create)} files.")
        return str(project_dir), None
    except Exception as e:
        error_msg = f"Failed during project file creation: {e}"
        print(f"DOCKER_UTILS ERROR: {error_msg}")
        if project_dir and project_dir.exists():
            shutil.rmtree(project_dir)
        return None, error_msg

def create_and_pack_git_repo(project_data: Dict[str, Any]) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Creates a git repo in a temporary directory, packs it, and returns the binary blob.
    IMPORTANT: This function no longer cleans up the temporary directory it creates.
    The calling process is now responsible for cleanup after it's done using the directory.
    """
    project_dir = None
    try:
        project_dir, error = create_project_directory_and_files(project_data)
        if error:
            raise Exception(error)
        
        meta_data = {
            "file_order": [file_info.get("path") for file_info in project_data.get("files", [])]
        }
        meta_file_path = Path(project_dir) / ".tesseracs_meta.json"
        with open(meta_file_path, 'w', encoding='utf-8') as f:
            json.dump(meta_data, f)

        subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "AI Assistant"], cwd=project_dir, check=True)
        subprocess.run(["git", "config", "user.email", "ai@tesseracs.com"], cwd=project_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=project_dir, check=True)
        commit_message = f"Initial commit: Create project '{project_data.get('name', 'Untitled Project')}'"
        subprocess.run(["git", "commit", "-m", commit_message], cwd=project_dir, check=True, capture_output=True, text=True)
        
        in_memory_file = io.BytesIO()
        with tarfile.open(fileobj=in_memory_file, mode="w:gz") as tar:
            tar.add(project_dir, arcname=os.path.basename(project_dir))
        
        return in_memory_file.getvalue(), None
    except Exception as e:
        error_msg = f"Failed during project creation/packing: {e}"
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        # Still clean up on FAILURE
        if project_dir and os.path.exists(project_dir):
            shutil.rmtree(project_dir)
        return None, error_msg

def unpack_git_repo_from_blob(repo_blob: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Takes a gzipped tarball blob, unpacks it to a temporary directory,
    reads all the file contents and metadata, gets commit history,
    cleans up, and returns a dictionary of project details.
    """
    try:
        in_memory_file = io.BytesIO(repo_blob)
        with tempfile.TemporaryDirectory() as unpack_dir:
            with tarfile.open(fileobj=in_memory_file, mode="r:gz") as tar:
                def is_within_directory(directory, target):
                    abs_directory = os.path.abspath(directory)
                    abs_target = os.path.abspath(target)
                    prefix = os.path.commonprefix([abs_directory, abs_target])
                    return prefix == abs_directory
                
                for member in tar.getmembers():
                    member_path = os.path.join(unpack_dir, member.name)
                    if not is_within_directory(unpack_dir, member_path):
                        raise Exception("Attempted Path Traversal in Tar File")
                
                tar.extractall(path=unpack_dir)

            unpacked_contents = os.listdir(unpack_dir)
            project_root_path = unpack_dir
            if len(unpacked_contents) == 1 and os.path.isdir(os.path.join(unpack_dir, unpacked_contents[0])):
                project_root_path = os.path.join(unpack_dir, unpacked_contents[0])
            
            # --- Get Commit History ---
            commits = []
            try:
                log_result = subprocess.run(
                    ['git', 'log', '--pretty=format:%H;%s;%ct'], # Use %ct for committer timestamp
                    cwd=project_root_path, capture_output=True, text=True, check=True, encoding='utf-8'
                )
                for line in log_result.stdout.strip().split('\n'):
                    if not line: continue
                    parts = line.split(';', 2)
                    if len(parts) == 3:
                        commits.append({
                            "hash": parts[0],
                            "message": parts[1],
                            "timestamp": int(parts[2]) * 1000 # to JS ms
                        })
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print(f"Warning: Could not get git log: {e}")

            # Read the metadata file to get the correct file order
            meta_file_path = os.path.join(project_root_path, '.tesseracs_meta.json')
            file_order_map = {}
            if os.path.exists(meta_file_path):
                with open(meta_file_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                    ordered_paths = meta_data.get("file_order", [])
                    file_order_map = {path: i for i, path in enumerate(ordered_paths)}

            files_list = []
            for root, dirs, files in os.walk(project_root_path):
                if '.git' in dirs:
                    dirs.remove('.git')
                
                for filename in files:
                    # Filter out the metadata file from the final list
                    if filename == '.tesseracs_meta.json':
                        continue

                    file_path_on_disk = os.path.join(root, filename)
                    normalized_path = os.path.relpath(file_path_on_disk, project_root_path).replace('\\', '/')
                    relative_path = f"./{normalized_path}"
                    
                    try:
                        with open(file_path_on_disk, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        stats = os.stat(file_path_on_disk)
                        files_list.append({
                            'path': relative_path, 
                            'content': content,
                            'size': stats.st_size,
                            'lastModified': stats.st_mtime * 1000 # To JS timestamp
                        })
                    except UnicodeDecodeError:
                        print(f"Warning: Could not read file {relative_path} as UTF-8 text.")

            # Sort the collected files based on the order from the metadata
            if file_order_map:
                files_list.sort(key=lambda f: file_order_map.get(f['path'], float('inf')))
            else:
                # Fallback to alphabetical sorting if metadata is missing
                files_list.sort(key=lambda f: f['path'])
            
            return {"files": files_list, "commits": commits}, None
            
    except Exception as e:
        error_msg = f"Failed to unpack and read git repo blob: {e}"
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        return None, error_msg

def get_project_state_at_commit(repo_blob: bytes, commit_hash: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    temp_dir_to_clean = None
    try:
        project_root, error = unpack_git_repo_to_temp_dir(repo_blob)
        if error or not project_root:
            raise Exception(f"Failed to unpack repo for commit checkout: {error}")
        
        temp_dir_to_clean = os.path.dirname(project_root)

        subprocess.run(['git', 'checkout', commit_hash], cwd=project_root, check=True, capture_output=True, text=True)

        meta_file_path = os.path.join(project_root, '.tesseracs_meta.json')
        file_order_map = {}
        if os.path.exists(meta_file_path):
            with open(meta_file_path, 'r', encoding='utf-8') as f:
                meta_data = json.load(f)
                ordered_paths = meta_data.get("file_order", [])
                file_order_map = {path: i for i, path in enumerate(ordered_paths)}

        files_list = []
        for root, dirs, files in os.walk(project_root):
            if '.git' in dirs:
                dirs.remove('.git')
            
            for filename in files:
                if filename == '.tesseracs_meta.json':
                    continue

                file_path_on_disk = os.path.join(root, filename)
                normalized_path = os.path.relpath(file_path_on_disk, project_root).replace('\\', '/')
                relative_path = f"./{normalized_path}"
                
                try:
                    with open(file_path_on_disk, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    stats = os.stat(file_path_on_disk)
                    files_list.append({
                        'path': relative_path, 
                        'content': content,
                        'language': get_language_from_extension(filename),
                        'size': stats.st_size,
                        'lastModified': stats.st_mtime * 1000
                    })
                except UnicodeDecodeError:
                    print(f"Warning: Could not read file {relative_path} as UTF-8 text.")

        if file_order_map:
            files_list.sort(key=lambda f: file_order_map.get(f['path'], float('inf')))
        else:
            files_list.sort(key=lambda f: f['path'])
        
        return files_list, None
    except Exception as e:
        return None, str(e)
    finally:
        if temp_dir_to_clean and os.path.exists(temp_dir_to_clean):
            shutil.rmtree(temp_dir_to_clean)

def add_output_and_commit(project_path: str, output: str, user_name: str) -> Optional[str]:
    try:
        output_file_path = os.path.join(project_path, "_run_output.log")
        with open(output_file_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(output)

        meta_file_path = os.path.join(project_path, '.tesseracs_meta.json')
        if os.path.exists(meta_file_path):
            with open(meta_file_path, 'r+', encoding='utf-8') as f:
                meta_data = json.load(f)
                file_order = meta_data.get("file_order", [])
                
                output_log_path = './_run_output.log'
                run_sh_path = './run.sh'

                if output_log_path in file_order:
                    file_order.remove(output_log_path)
                
                try:
                    run_sh_index = file_order.index(run_sh_path)
                    file_order.insert(run_sh_index + 1, output_log_path)
                except ValueError:
                    file_order.append(output_log_path)
                
                meta_data["file_order"] = file_order
                f.seek(0)
                json.dump(meta_data, f, indent=4)
                f.truncate()
        
        status_result = subprocess.run(
            ["git", "status", "--porcelain"], 
            cwd=project_path, check=True, capture_output=True, text=True
        )
        if not status_result.stdout.strip():
            return None

        subprocess.run(["git", "config", "user.name", user_name], cwd=project_path, check=True)
        subprocess.run(["git", "config", "user.email", f"{user_name.replace(' ', '.')}@tesseracs.dev"], cwd=project_path, check=True)
        
        subprocess.run(["git", "add", "_run_output.log", ".tesseracs_meta.json"], cwd=project_path, check=True)
        
        status_result_after_add = subprocess.run(
            ["git", "diff", "--staged", "--quiet"], 
            cwd=project_path
        )
        
        if status_result_after_add.returncode == 0:
            return None

        commit_message = "Capture code execution output"
        subprocess.run(["git", "commit", "-m", commit_message], cwd=project_path, check=True)
        
        return None
    except subprocess.CalledProcessError as e:
        error_msg = f"Git operation failed: {e.stderr}"
        print(f"PROJECT_UTILS GIT ERROR: {error_msg}")
        return error_msg
    except Exception as e:
        return str(e)

def repack_repo_from_path(project_path: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        in_memory_file = io.BytesIO()
        with tarfile.open(fileobj=in_memory_file, mode="w:gz") as tar:
            tar.add(project_path, arcname=os.path.basename(project_path))
        return in_memory_file.getvalue(), None
    except Exception as e:
        return None, str(e)

def unpack_git_repo_from_blob(repo_blob: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        in_memory_file = io.BytesIO(repo_blob)
        with tempfile.TemporaryDirectory() as unpack_dir:
            with tarfile.open(fileobj=in_memory_file, mode="r:gz") as tar:
                def is_within_directory(directory, target):
                    abs_directory = os.path.abspath(directory)
                    abs_target = os.path.abspath(target)
                    prefix = os.path.commonprefix([abs_directory, abs_target])
                    return prefix == abs_directory
                
                for member in tar.getmembers():
                    member_path = os.path.join(unpack_dir, member.name)
                    if not is_within_directory(unpack_dir, member_path):
                        raise Exception("Attempted Path Traversal in Tar File")
                
                tar.extractall(path=unpack_dir)

            unpacked_contents = os.listdir(unpack_dir)
            project_root_path = unpack_dir
            if len(unpacked_contents) == 1 and os.path.isdir(os.path.join(unpack_dir, unpacked_contents[0])):
                project_root_path = os.path.join(unpack_dir, unpacked_contents[0])
            
            commits = []
            try:
                log_result = subprocess.run(
                    ['git', 'log', '--pretty=format:%H;%s;%ct'],
                    cwd=project_root_path, capture_output=True, text=True, check=True, encoding='utf-8'
                )
                for line in log_result.stdout.strip().split('\n'):
                    if not line: continue
                    parts = line.split(';', 2)
                    if len(parts) == 3:
                        commits.append({
                            "hash": parts[0],
                            "message": parts[1],
                            "timestamp": int(parts[2]) * 1000
                        })
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print(f"Warning: Could not get git log: {e}")

            meta_file_path = os.path.join(project_root_path, '.tesseracs_meta.json')
            file_order_map = {}
            if os.path.exists(meta_file_path):
                with open(meta_file_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                    ordered_paths = meta_data.get("file_order", [])
                    file_order_map = {path: i for i, path in enumerate(ordered_paths)}

            files_list = []
            for root, dirs, files in os.walk(project_root_path):
                if '.git' in dirs:
                    dirs.remove('.git')
                
                for filename in files:
                    if filename == '.tesseracs_meta.json':
                        continue

                    file_path_on_disk = os.path.join(root, filename)
                    normalized_path = os.path.relpath(file_path_on_disk, project_root_path).replace('\\', '/')
                    relative_path = f"./{normalized_path}"
                    
                    try:
                        with open(file_path_on_disk, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        stats = os.stat(file_path_on_disk)
                        files_list.append({
                            'path': relative_path, 
                            'content': content,
                            'size': stats.st_size,
                            'lastModified': stats.st_mtime * 1000
                        })
                    except UnicodeDecodeError:
                        print(f"Warning: Could not read file {relative_path} as UTF-8 text.")

            if file_order_map:
                files_list.sort(key=lambda f: file_order_map.get(f['path'], float('inf')))
            else:
                files_list.sort(key=lambda f: f['path'])
            
            return {"files": files_list, "commits": commits}, None
            
    except Exception as e:
        error_msg = f"Failed to unpack and read git repo blob: {e}"
        print(f"PROJECT_UTILS ERROR: {error_msg}")
        return None, error_msg
    
#this is the end