# update_dockerfiles.ps1
# This script creates or overwrites all Dockerfiles with their final, correct versions.

Write-Host "--- Updating all Dockerfiles with correct package names ---" -ForegroundColor Green
Write-Host ""

# Helper function to create/overwrite a Dockerfile
function Set-DockerfileContent {
    param(
        [string]$DockerfilePath,
        [string]$Content
    )
    
    $DirectoryPath = Split-Path -Path $DockerfilePath -Parent
    if (-not (Test-Path $DirectoryPath)) {
        Write-Host "Creating directory: $DirectoryPath"
        New-Item -ItemType Directory -Path $DirectoryPath | Out-Null
    }
    
    Write-Host "Writing to file: $DockerfilePath"
    $Content.Trim() | Set-Content -Path $DockerfilePath -Force
    
    if (-not $?) {
        Write-Error "Failed to write to $DockerfilePath"
        exit 1
    }
}

# --- Define Content for Each Dockerfile ---

# Base Python image (already includes Python and uv)
$pythonDockerfile = @"
# dockerfiles/python/Dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install uv
CMD ["/bin/sh"]
"@

# C/C++ Image (built from the Python base)
$gccDockerfile = @"
# dockerfiles/gcc/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ make && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Node.js/Web Image (built from the Python base)
$nodeDockerfile = @"
# dockerfiles/node/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Java Image (built from the Python base)
$javaDockerfile = @"
# dockerfiles/java/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
# Use the 'default-jdk' package which is the standard for Debian
RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jdk && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Rust Image (built from the Python base)
$rustDockerfile = @"
# dockerfiles/rust/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends rustc cargo curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Go Image (built from the Python base)
$goDockerfile = @"
# dockerfiles/go/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends golang && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# .NET/C# Image (built from the Python base)
$dotnetDockerfile = @"
# dockerfiles/dotnet/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies needed for the .NET install script
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl python3-venv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install .NET SDK using the official script to avoid apt dependency issues
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin --channel 7.0

# Create a virtual environment for Python tools
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv `$VIRTUAL_ENV

# Add both .NET and the Python venv to the PATH
ENV PATH="/root/.dotnet:`$VIRTUAL_ENV/bin:`$PATH"

# Install uv into the venv
RUN pip install uv
"@


# --- Write all the files ---
Set-DockerfileContent -DockerfilePath "./dockerfiles/python/Dockerfile" -Content $pythonDockerfile
Set-DockerfileContent -DockerfilePath "./dockerfiles/gcc/Dockerfile" -Content $gccDockerfile
Set-DockerfileContent -DockerfilePath "./dockerfiles/node/Dockerfile" -Content $nodeDockerfile
Set-DockerfileContent -DockerfilePath "./dockerfiles/java/Dockerfile" -Content $javaDockerfile
Set-DockerfileContent -DockerfilePath "./dockerfiles/rust/Dockerfile" -Content $rustDockerfile
Set-DockerfileContent -DockerfilePath "./dockerfiles/go/Dockerfile" -Content $goDockerfile
Set-DockerfileContent -DockerfilePath "./dockerfiles/dotnet/Dockerfile" -Content $dotnetDockerfile

Write-Host ""
Write-Host "--- All Dockerfiles have been updated successfully! ---" -ForegroundColor Green