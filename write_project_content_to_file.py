import os
import sys

# --- Configuration ---

# Set the starting directory (e.g., "." for current directory)
root = "."


import os

# --- Configuration ---

# !!! CHANGE THIS VARIABLE TO THE DESIRED STARTING DIRECTORY !!!
# Example: root = "/path/to/your/project" or root = "src"
root = fr"./"

# Extensions to include
extensions = ('.py', '.html', '.ps1', '.css', '.js', '.json', '.md', '.txt', '.yaml', '.yml', '.toml')

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
exclude_exact_filenames = (
    'package-lock.json',
    'yarn.lock',
    'katex.min.css',
    'katex.min.js',
    'build.js',
    "build_all_images.ps1",
    "create_dockerfiles.ps1"
    # Add other large or irrelevant files by name here
)

# Files to exclude from the tree and content if their name contains any of these strings
exclude_filename_patterns = ('lock',)

# Specific files to list in the tree but exclude their *content* from the output
exclude_files = (
    'session.json',
    'cm_logo.ico',
)

# Output file name
output_filename = "project_content.txt"

# --- Script Logic ---

# Normalize the root path and make it absolute for reliable comparisons
root = os.path.abspath(root)

print(f"Starting directory: {root}")
print(f"Output file: {output_filename}")

# The instructions to be added at the end of the file
llm_instructions = """
=== LLM Instructions ===
Now please just read the project and the rules for writing code and just wait for instructions.

Here are the rules:
1. DONT write comments, placeholders or docstrings if not explicitely told.
2. Write functions with the correct initial indentation so that if the function is indented so is your code that you write.
3. DO NOT USE NON-TERMINATED SPACES. MAKE SURE TO FOLLOW THIS! ITS SUPER IMPORTANT AND YOU SEEM TO BREAK IT, PLEASE!!!
4. For changes that require multiple replacements please tell me what to replace with what instead of rewriting large portions of text. You can use vscode valid regexp for instance.
5. For small functions less than 100 lines of code please rewrite the full function. For larger functions please only write complete control statements if/while/case.
6. Never omit/change working logic if not explicitely statet that it should be removed/change
7. DONT use the CANVAS TOOL where code is written in artifacts.
8. Write only functions that are new seperately from functions that needs updates.
9. Make sure to write what has been change where to place/what to replace for each code snippet.
"""


try:
    # Using "w" mode ensures the file is overwritten if it exists
    with open(output_filename, "w", encoding="utf-8") as outfile:
        # === Add Directory Structure ===
        outfile.write("=== Project Directory Structure ===\n")
        outfile.write(f"Root: {root}\n")
        outfile.write("Relevant files and folders (excluding specified patterns):\n\n")

        start_level = root.count(os.sep)
        for current_root, dirs, files in os.walk(root, topdown=True):
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
            rel_path_from_start = os.path.relpath(current_root, root)
            level = current_root.count(os.sep) - start_level

            if rel_path_from_start != '.':
                path_components = os.path.normpath(rel_path_from_start).split(os.sep)
                if any(comp in exclude_dirs or comp.startswith('.') for comp in path_components):
                    continue

                indent = '│   ' * (level - 1) + '├── ' if level > 0 else ''
                outfile.write(f"{indent}{os.path.basename(current_root)}/\n")
            else:
                outfile.write(".\n")

            file_indent = '│   ' * level + '├── '
            files.sort()
            for file in files:
                # Apply all file exclusion rules
                if (file.endswith(extensions) and
                    not file.startswith('.') and
                    file not in exclude_exact_filenames and
                    not any(p in file for p in exclude_filename_patterns)):
                        outfile.write(f"{file_indent}{file}\n")

        outfile.write("\n\n=== File Contents ===\n\n")

        # === Add File Contents ===
        for current_root, dirs, files in os.walk(root, topdown=True):
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
            rel_path_from_start = os.path.relpath(current_root, root)
            if rel_path_from_start != '.':
                path_components = os.path.normpath(rel_path_from_start).split(os.sep)
                if any(comp in exclude_dirs or comp.startswith('.') for comp in path_components):
                    continue

            files.sort()
            for file in files:
                # Apply all file exclusion rules again for content processing
                if (file.endswith(extensions) and
                    not file.startswith('.') and
                    file not in exclude_exact_filenames and
                    not any(p in file for p in exclude_filename_patterns)):
                        file_path = os.path.join(current_root, file)
                        relative_path = os.path.relpath(file_path, root)
                        display_path = relative_path.replace(os.sep, '/')
                        outfile.write(f"=== {display_path} ===\n")

                        if file in exclude_files:
                            outfile.write("--- CONTENT EXCLUDED (listed in exclude_files) ---\n")
                        else:
                            try:
                                try:
                                    with open(file_path, "r", encoding="utf-8") as infile:
                                        outfile.write(infile.read())
                                except UnicodeDecodeError:
                                    try:
                                        with open(file_path, "r", encoding="latin-1") as infile:
                                            outfile.write(infile.read())
                                        outfile.write("\n--- (Warning: Read using latin-1 encoding) ---\n")
                                    except Exception as inner_e:
                                        outfile.write(f"--- Error reading file (fallback failed): {inner_e} ---\n")
                            except Exception as e:
                                outfile.write(f"--- Error reading file: {e} ---\n")
                        outfile.write("\n\n")

        # === Add LLM Instructions at the end of the file ===
        outfile.write(llm_instructions)

    print("Successfully generated project content file.")

except Exception as e:
    print(f"An error occurred: {e}")