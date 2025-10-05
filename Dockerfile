FROM python:3.11-slim-bookworm

# --- CHANGE: Install 'git' which is required by project_utils.py ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends git coreutils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv, the fast package installer
RUN pip install uv

# Set the working directory inside the container
WORKDIR /code

# Create a virtual environment using uv
RUN uv venv

# Copy dependency files first to leverage Docker cache
COPY pyproject.toml poetry.lock* uv.lock* Readme.md ./

# Install project dependencies into the virtual environment
RUN uv pip install --no-cache-dir .

# Copy the rest of the application code
COPY . .

    

