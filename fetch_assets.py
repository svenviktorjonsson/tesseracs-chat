import os
import requests
import sys
import shutil
import subprocess
import tempfile
import zipfile
import glob

# --- Configuration ---
script_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(script_dir, 'app', 'static')
assets_dir = os.path.join(static_dir, 'assets')
css_dir = os.path.join(assets_dir, 'css')
fonts_dir = os.path.join(assets_dir, 'fonts')
js_dir = os.path.join(static_dir, 'js')

# --- Files to download directly from the web ---
files_to_download = [
    {
        'name': 'katex.min.css',
        'url': 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css',
        'dest': os.path.join(css_dir, 'katex.min.css')
    },
    {
        'name': 'prism-tomorrow.min.css',
        'url': 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css',
        'dest': os.path.join(css_dir, 'prism-tomorrow.min.css')
    },
    {
        'name': 'd3.min.js',
        'url': 'https://cdnjs.cloudflare.com/ajax/libs/d3/4.13.0/d3.min.js',  # D3 v4 for mpld3 compatibility
        'dest': os.path.join(js_dir, 'd3.min.js')
    }
]

# --- Files to extract from Python packages ---
files_to_extract_from_packages = [
    {
        'package_name': 'mpld3',
        'file_to_find': 'mpld3.min.js',
        'dest_path': os.path.join(js_dir, 'mpld3.min.js')
    }
]

# --- Helper Functions ---
def ensure_dir_exists(dir_path):
    if not os.path.exists(dir_path):
        print(f"Creating directory: {dir_path}")
        os.makedirs(dir_path, exist_ok=True)

def download_file(url, destination_path):
    print(f"Attempting to download {os.path.basename(destination_path)} from {url}...")
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(destination_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Successfully downloaded: {os.path.basename(destination_path)}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {url}: {e}", file=sys.stderr)
        return False

def extract_from_package(package_name, file_to_find, dest_path):
    """
    Downloads a package, intelligently finds a versioned file within it,
    extracts it, and then cleans up.
    """
    print(f"Attempting to extract {os.path.basename(dest_path)} from '{package_name}' package...")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Step 1: Download the package wheel to the temporary directory
            print(f"  - Downloading '{package_name}' package...")
            subprocess.run(
                [sys.executable, '-m', 'pip', 'download', '--no-deps', package_name, '-d', tmpdir],
                check=True, capture_output=True, text=True
            )

            wheel_files = glob.glob(os.path.join(tmpdir, '*.whl'))
            if not wheel_files:
                print(f"Error: Could not find wheel file for '{package_name}' in {tmpdir}", file=sys.stderr)
                return False
            wheel_file_path = wheel_files[0]
            print(f"  - Found package file: {os.path.basename(wheel_file_path)}")

            # Step 2: Open the wheel and intelligently search for the target file
            source_path_in_archive = None
            # Construct a search pattern, e.g., 'mpld3.v' and '.min.js'
            base_name, extension = os.path.splitext(file_to_find)
            base_name, min_ext = os.path.splitext(base_name)

            with zipfile.ZipFile(wheel_file_path, 'r') as wheel_zip:
                file_list = wheel_zip.namelist()
                for member in file_list:
                    # Look for a file that starts with the base name and version prefix
                    # and ends with the correct minified extension.
                    if member.startswith(f'{package_name}/js/{base_name}.v') and member.endswith(f'{min_ext}{extension}'):
                        source_path_in_archive = member
                        break
                
                if not source_path_in_archive:
                    print(f"Error: Could not find a version of '{file_to_find}' inside the package archive.", file=sys.stderr)
                    print("\n--- Archive Contents ---")
                    for file_name in sorted(file_list):
                        print(file_name)
                    print("------------------------\n")
                    return False
                
                print(f"  - Found asset at dynamic path: '{source_path_in_archive}'")

                # Step 3: Extract the found file to the final destination
                with wheel_zip.open(source_path_in_archive) as source_file:
                    with open(dest_path, 'wb') as dest_file:
                        shutil.copyfileobj(source_file, dest_file)
                
                print(f"  - Successfully extracted file to {dest_path}")
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error: Failed to download '{package_name}' with pip.", file=sys.stderr)
            print(f"PIP Stderr: {e.stderr}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"An unexpected error occurred during extraction: {e}", file=sys.stderr)
            return False

def main():
    print("Starting asset fetching process...")
    total_success = 0
    total_failure = 0

    print("Ensuring necessary directories exist...")
    ensure_dir_exists(static_dir)
    ensure_dir_exists(assets_dir)
    ensure_dir_exists(css_dir)
    ensure_dir_exists(fonts_dir)
    ensure_dir_exists(js_dir)
    print("Directory check complete.")

    print("\nDownloading assets from the web...")
    for file_info in files_to_download:
        if download_file(file_info['url'], file_info['dest']):
            total_success += 1
        else:
            total_failure += 1

    print("\nExtracting assets from Python packages...")
    for file_info in files_to_extract_from_packages:
        if extract_from_package(file_info['package_name'], file_info['file_to_find'], file_info['dest_path']):
            total_success += 1
        else:
            total_failure += 1

    print(f"\nAsset fetching summary: {total_success} succeeded, {total_failure} failed.")

    if total_failure > 0:
        print("\nOne or more asset operations failed. Please check the errors above.", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nAsset fetching process completed successfully.")

if __name__ == "__main__":
    main()