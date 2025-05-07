import os
import requests
import sys

# --- Configuration ---
# Get the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
print(f"DEBUG: Script is running from directory: {script_dir}") # <-- Added Debug Print

# Define paths relative to the script's location
static_dir = os.path.join(script_dir, 'app', 'static')
assets_dir = os.path.join(static_dir, 'assets')
css_dir = os.path.join(assets_dir, 'css')
fonts_dir = os.path.join(assets_dir, 'fonts') # Define fonts dir for creation

# Print the calculated target directories for verification
print(f"DEBUG: Target static directory: {static_dir}")
print(f"DEBUG: Target assets directory: {assets_dir}")
print(f"DEBUG: Target CSS directory: {css_dir}")
print(f"DEBUG: Target fonts directory: {fonts_dir}") # <-- Added Debug Print

# List of CSS files to download
css_files_to_download = [
    {
        'name': 'katex.min.css',
        'url': 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css',
        'dest': os.path.join(css_dir, 'katex.min.css')
    },
    {
        'name': 'prism-tomorrow.min.css',
        'url': 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css',
        'dest': os.path.join(css_dir, 'prism-tomorrow.min.css')
    }
]

# --- Helper Functions ---
def ensure_dir_exists(dir_path):
    """Creates a directory if it doesn't exist."""
    if not os.path.exists(dir_path):
        print(f"Creating directory: {dir_path}")
        try:
            os.makedirs(dir_path, exist_ok=True)
        except OSError as e:
            print(f"Error creating directory {dir_path}: {e}", file=sys.stderr)
            sys.exit(1) # Exit if directory creation fails

def download_file(url, destination_path):
    """Downloads a file from a URL to a destination path."""
    print(f"Attempting to download {os.path.basename(destination_path)} to {destination_path} from {url}...") # <-- Added Debug Print
    try:
        response = requests.get(url, stream=True, timeout=30) # Added timeout
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        with open(destination_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Successfully downloaded: {os.path.basename(destination_path)}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {url}: {e}", file=sys.stderr)
        # Clean up partially downloaded file if it exists
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
                print(f"Removed partially downloaded file: {os.path.basename(destination_path)}")
            except OSError as remove_error:
                print(f"Error removing partial file {destination_path}: {remove_error}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"An unexpected error occurred during download of {url}: {e}", file=sys.stderr)
        return False


# --- Main Execution ---
def main():
    print("Starting Python asset fetching process...")

    # 1. Ensure directories exist
    print("Ensuring necessary directories exist...")
    ensure_dir_exists(static_dir)
    ensure_dir_exists(assets_dir)
    ensure_dir_exists(css_dir)
    ensure_dir_exists(fonts_dir) # Also create fonts dir
    print("Directory check complete.")

    # 2. Download CSS files
    print("\nDownloading CSS files...")
    success_count = 0
    failure_count = 0
    for file_info in css_files_to_download:
        # Print the destination path before downloading
        print(f"DEBUG: Calculated destination for {file_info['name']}: {file_info['dest']}") # <-- Added Debug Print
        if download_file(file_info['url'], file_info['dest']):
            success_count += 1
        else:
            failure_count += 1

    print(f"\nDownload summary: {success_count} succeeded, {failure_count} failed.")

    if failure_count > 0:
        print("\nOne or more downloads failed. Please check the errors above.", file=sys.stderr)
        sys.exit(1) # Exit with error code if any download failed
    else:
        print("\nPython asset fetching process completed successfully.")
        print("CSS files downloaded to:", css_dir)
        print("Ensure font files are placed in:", fonts_dir)

if __name__ == "__main__":
    main()
