import os
import sys

# --- Configuration ---

# Set the starting directory (e.g., "." for current directory)
root = "."

# Extensions to include
extensions = ('.py', '.html', '.css', '.js', '.json', '.md', '.txt', '.yaml', '.yml', '.toml')

# Directories to completely exclude (will not be walked)
exclude_dirs = (
    '.git',
    '__pycache__',
    '.pytest_cache',
    'node_modules',
    'build',
    'old2',
    'testing-bundles', # Exclude this directory name wherever it appears
    '.venv',           # Common virtual environment folder
    'venv',
    'env',
    '.env',
    'dist',
    "assets"
)

# Files to list in the tree but exclude their *content*
exclude_files = (
    'package-lock.json',
    'yarn.lock',
    'katex.min.css',
    'katex.min.js',
    'build.js',
    'docker_utils.js',
    'llm.py',
    'input.css'
    # Add other large or irrelevant files by name here
)

# Output file name
output_filename = "project_content.txt"

# --- Script Logic ---

try:
    root = os.path.abspath(root)
    print(f"Starting directory: {root}")
    print(f"Output file: {output_filename}")
    print(f"Excluding directories: {exclude_dirs}")
    print(f"Excluding content of files: {exclude_files}")

    # Use a set for faster lookups
    exclude_dirs_set = set(exclude_dirs)
    exclude_files_set = set(exclude_files)

    with open(output_filename, "w", encoding="utf-8", errors="replace") as outfile:
        # === Add Directory Structure ===
        outfile.write("=== Project Directory Structure ===\n")
        outfile.write(f"Root: {root}\n")
        outfile.write("Relevant files and folders (excluding specified patterns):\n\n")

        structure_lines = []
        processed_dirs_for_structure = set()

        for current_root, dirs, files in os.walk(root, topdown=True):
            # Filter directories *in place* to prevent walking into excluded ones
            # Also exclude hidden directories starting with '.' unless root is hidden
            original_dirs = list(dirs) # Keep original for path checking if needed
            dirs[:] = [d for d in dirs if d not in exclude_dirs_set and not (d.startswith('.') and d != '.')]

            rel_path_from_start = os.path.relpath(current_root, root)

            # --- Skip processing if current path is inside an excluded directory ---
            # Check if any component of the relative path is in the exclusion list
            # Normalize path separators for consistent checking
            norm_rel_path = os.path.normpath(rel_path_from_start).replace(os.sep, '/')
            path_components = set(comp for comp in norm_rel_path.split('/') if comp and comp != '.')

            if any(comp in exclude_dirs_set or (comp.startswith('.') and comp != '.') for comp in path_components):
                 continue # Skip this directory entirely if any parent was excluded

            level = norm_rel_path.count('/') if norm_rel_path != '.' else 0

            # Add directory entry
            if rel_path_from_start == '.':
                 structure_lines.append(".\n")
            else:
                 indent = '│   ' * (level - 1) + '├── ' if level > 0 else ''
                 dir_name = os.path.basename(current_root)
                 # Check if this specific dir name should be excluded (e.g., if it's at the root)
                 if dir_name not in exclude_dirs_set and not (dir_name.startswith('.') and dir_name != '.'):
                     structure_lines.append(f"{indent}{dir_name}/\n")
                     processed_dirs_for_structure.add(norm_rel_path)


            # Add file entries for this directory
            file_indent = '│   ' * level + '├── '
            files.sort()
            for file in files:
                if file.endswith(extensions) and not file.startswith('.'):
                     structure_lines.append(f"{file_indent}{file}\n")

        # Write collected structure lines
        for line in structure_lines:
             outfile.write(line)

        outfile.write("\n\n=== File Contents ===\n\n")

        # === Add File Contents ===
        for current_root, dirs, files in os.walk(root, topdown=True):
            # Apply the same directory filtering as in the first walk
            dirs[:] = [d for d in dirs if d not in exclude_dirs_set and not (d.startswith('.') and d != '.')]

            rel_path_from_start = os.path.relpath(current_root, root)

            # --- Skip processing if current path is inside an excluded/hidden directory ---
            norm_rel_path = os.path.normpath(rel_path_from_start).replace(os.sep, '/')
            path_components = set(comp for comp in norm_rel_path.split('/') if comp and comp != '.')
            if any(comp in exclude_dirs_set or (comp.startswith('.') and comp != '.') for comp in path_components):
                 continue # Skip files in this directory

            files.sort()
            for file in files:
                 # Exclude hidden files and check extensions
                 if file.endswith(extensions) and not file.startswith('.'):
                     file_path = os.path.join(current_root, file)
                     relative_path = os.path.relpath(file_path, root)
                     display_path = relative_path.replace(os.sep, '/')

                     outfile.write(f"=== {display_path} ===\n")

                     # Check if the file *content* should be excluded
                     if file in exclude_files_set:
                         outfile.write("--- CONTENT EXCLUDED (listed in exclude_files) ---\n")
                     else:
                         try:
                             # Try reading with utf-8 first
                             with open(file_path, "r", encoding="utf-8") as infile:
                                 outfile.write(infile.read())
                         except UnicodeDecodeError:
                             # Fallback for non-utf8 files
                             try:
                                 with open(file_path, "r", encoding="latin-1") as infile:
                                     outfile.write(infile.read())
                                 outfile.write("\n--- (Warning: Read using latin-1 encoding) ---\n")
                             except Exception as inner_e:
                                 outfile.write(f"--- Error reading file (fallback failed): {inner_e} ---\n")
                         except Exception as e:
                             # Handle other potential file reading errors
                             outfile.write(f"--- Error reading file: {e} ---\n")

                     outfile.write("\n\n") # Add separation between file contents

    print(f"Successfully generated project content file: {output_filename}")

except FileNotFoundError:
    print(f"Error: Starting directory not found: {root}", file=sys.stderr)
except IOError as e:
    print(f"Error writing to output file {output_filename}: {e}", file=sys.stderr)
except Exception as e:
    print(f"An unexpected error occurred: {e}", file=sys.stderr)