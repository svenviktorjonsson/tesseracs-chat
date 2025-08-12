# run_app.ps1
# PowerShell script to run the Tesseracs Chat FastAPI application
# using Poetry and Uvicorn.
# THIS VERSION RUNS WITHOUT --reload FOR CSRF TROUBLESHOOTING.

# Display a message to the user
Write-Host "Attempting to start the Tesseracs Chat FastAPI application (NO RELOAD)..."
Write-Host "Command: poetry run uvicorn app.main:app --host 127.0.0.1 --port 8001"
Write-Host "Press CTRL+C to stop the server."
Write-Host ""

# Execute the command
# This assumes that 'poetry' is in your PATH and you are running this script
# from the root directory of your project (where pyproject.toml is located).
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8001

# You can add more error handling or checks here if needed, for example:
# if ($LASTEXITCODE -ne 0) {
#     Write-Error "The application failed to start or exited with an error."
# } else {
#     Write-Host "Application stopped."
# }
