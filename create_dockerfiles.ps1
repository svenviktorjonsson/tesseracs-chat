# This script creates or overwrites all Dockerfiles with their final, correct versions.

Write-Host "--- Updating all Dockerfiles to include git ---" -ForegroundColor Green
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

# Base Python image
$pythonDockerfile = @"
# dockerfiles/python/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
CMD ["/bin/sh"]
"@

# C/C++ Image
$gccDockerfile = @"
# dockerfiles/gcc/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ make git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Node.js/Web Image
$nodeDockerfile = @"
# dockerfiles/node/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm curl git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Java Image
$javaDockerfile = @"
# dockerfiles/java/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jdk git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Rust Image
$rustDockerfile = @"
# dockerfiles/rust/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends rustc cargo curl git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# Go Image
$goDockerfile = @"
# dockerfiles/go/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends golang git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
"@

# .NET/C# Image
$dotnetDockerfile = @"
# dockerfiles/dotnet/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies needed for git and the .NET install script
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl python3-venv git && \
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