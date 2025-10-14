# Stage 1: Base - Common foundation for both dev and prod
FROM python:3.11-slim-bookworm AS base

# Install git, which is required by project_utils.py for repository operations
RUN apt-get update && \
    apt-get install -y --no-install-recommends git coreutils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv globally in the base image
RUN pip install uv

# Create a dedicated, non-mounted directory for the virtual environment
ENV VENV_PATH=/opt/venv
RUN uv venv $VENV_PATH

# FIX: Add the virtual environment's bin directory to the system's PATH.
# This makes `python`, `pytest`, `uvicorn`, etc., directly available.
ENV PATH="$VENV_PATH/bin:$PATH"

# Set the working directory for the application code
WORKDIR /app

# Copy dependency files
COPY pyproject.toml Readme.md ./

#--------------------------------------------------------------------------

# Stage 2: Development image - Includes all code and testing tools
FROM base AS dev

# Install all dependencies into the virtual environment.
# This no longer needs the full path or activation script because of the ENV PATH.
RUN uv pip install --no-cache-dir ".[dev]"

# Copy the rest of the application code
COPY . .

#--------------------------------------------------------------------------

# Stage 3: Production image - A lean image without dev dependencies
FROM base AS prod

# Install only production dependencies
RUN uv pip install --no-cache-dir "."

# Copy the application code
COPY . .