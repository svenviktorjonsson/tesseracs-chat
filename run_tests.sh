#!/bin/sh
echo "--- Running Pytest Suite ---"

# FIX: Add the application's root directory to the PYTHONPATH.
# This ensures that pytest can find and import the 'app' module.
export PYTHONPATH=/app

pytest

echo "--- Pytest Suite Finished ---"