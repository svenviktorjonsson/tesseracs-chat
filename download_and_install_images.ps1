# setup-docker-images.ps1

<#
.SYNOPSIS
Builds the custom 'node-ts:18' Docker image and downloads (pulls) other
Docker images required by the application's config.py.

.DESCRIPTION
This script performs a two-phase setup for Docker images:
1.  Build Phase: Creates a custom Docker image tagged 'node-ts:18' based on
    'node:18', installing TypeScript and dos2unix globally.
2.  Pull Phase: Downloads other required Docker images specified in a predefined
    list (extracted from the application's config.py).

It provides feedback on the success or failure of each phase and an overall summary.

.NOTES
- Requires Docker Desktop or Docker Engine to be installed and running.
- Network connection is required for both building the base layer and pulling images.
- Execution policy might need adjustment: `Set-ExecutionPolicy RemoteSigned` or `Bypass`.
  Run PowerShell as Administrator or use: `Set-ExecutionPolicy Bypass -Scope Process -Force`
#>

# --- Configuration ---

# Custom image details
$CustomImageTag = "node-ts:18"
$DockerfilePath = ".\Dockerfile.node-ts.temp" # Temporary file for building the custom image

# List of OTHER required Docker images (extracted from config.py, excluding the custom one)
$OtherImagesToPull = @(
    "python:3.11-slim",            # For python
    "gcc:latest",                  # For cpp
    "mcr.microsoft.com/dotnet/sdk:latest", # For csharp
    "openjdk:17-jdk-slim",         # For java
    "golang:1.21-alpine",          # For go
    "rust:1-slim"                  # For rust
) | Select-Object -Unique # Ensure the list is unique

# Flags to track success of each phase
$BuildSuccessful = $false
$PullSuccessful = $true # Assume true initially, set to false on any pull failure

# --- Phase 1: Build Custom node-ts:18 Image ---

Write-Host "--- Phase 1: Building Custom Image ($CustomImageTag) ---" -ForegroundColor Cyan
Write-Host "Ensure the Docker daemon is running."

# Define the Dockerfile content for the custom image
$DockerfileContent = @"
# Base image: Official Node.js 18 LTS (Debian-based)
FROM node:18

# Install TypeScript globally using npm
# --no-fund and --no-audit can make installs slightly faster/cleaner in build environments
RUN npm install -g typescript --no-fund --no-audit

# Update package list and install dos2unix
# dos2unix is helpful for converting CRLF (Windows) line endings to LF (Unix) if needed
# Clean up apt cache afterwards to keep the image size smaller
RUN apt-get update && \
    apt-get install -y --no-install-recommends dos2unix && \
    rm -rf /var/lib/apt/lists/*

# Set the default working directory inside the container
WORKDIR /app
"@

try {
    # Write the Dockerfile content to the temporary file
    Write-Host "Creating temporary Dockerfile: $DockerfilePath"
    $DockerfileContent | Out-File -FilePath $DockerfilePath -Encoding UTF8 -ErrorAction Stop
    Write-Host "Dockerfile created."

    # Execute the Docker build command
    Write-Host "Starting Docker build process for $CustomImageTag..."
    # -t tags the image
    # -f specifies the Dockerfile path
    # . specifies the build context (current directory)
    docker build -t $CustomImageTag -f $DockerfilePath .

    # Check the success of the last command ($?)
    if ($?) {
        Write-Host "Successfully built image: $CustomImageTag" -ForegroundColor Green
        $BuildSuccessful = $true
    } else {
        # Docker build command itself usually outputs detailed errors
        Write-Host "Failed to build image: $CustomImageTag. Check Docker output above for details." -ForegroundColor Red
        $BuildSuccessful = $false
    }
} catch {
    # Catch errors related to file operations or potentially Docker command execution
    Write-Host "An error occurred during the build process:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    $BuildSuccessful = $false
} finally {
    # Clean up the temporary Dockerfile if it exists
    if (Test-Path $DockerfilePath) {
        Write-Host "Cleaning up temporary Dockerfile: $DockerfilePath"
        Remove-Item $DockerfilePath
    }
}
Write-Host "--- Phase 1 Finished ---"


# --- Phase 2: Pull Other Required Images ---

Write-Host " " # Spacer
Write-Host "--- Phase 2: Pulling Other Required Images ---" -ForegroundColor Cyan
Write-Host "Found $($OtherImagesToPull.Count) other images to pull."

if (-not $OtherImagesToPull) {
    Write-Host "No other images specified to pull."
    # In this specific case, $PullSuccessful remains $true as there was nothing to fail.
} else {
    # Loop through each unique image name to pull
    foreach ($imageName in $OtherImagesToPull) {
        Write-Host " " # Spacer
        Write-Host "Attempting to pull image: $imageName ..."
        try {
            # Execute the docker pull command
            docker pull $imageName

            # Check the success of the last command ($?)
            if ($?) {
                Write-Host "Successfully pulled or verified image: $imageName" -ForegroundColor Green
            } else {
                # Docker pull command itself usually provides detailed error messages
                Write-Host "Failed to pull image: $imageName. Docker command finished with an error." -ForegroundColor Red
                Write-Host "Check Docker output above for details." -ForegroundColor Yellow
                $PullSuccessful = $false # Mark overall pull phase as failed
                # Optional: Stop the script on the first pull failure
                # Write-Host "Stopping script due to pull error." -ForegroundColor Red
                # break
            }
        } catch {
            # Catch errors if the 'docker' command itself cannot be executed
            Write-Host "An critical error occurred trying to execute 'docker pull $imageName':" -ForegroundColor Red
            Write-Host $_.Exception.Message -ForegroundColor Red
            Write-Host "Is Docker installed and the Docker daemon running?" -ForegroundColor Yellow
            $PullSuccessful = $false # Mark overall pull phase as failed
            # Optional: Stop the script on critical pull failure
            # Write-Host "Stopping script due to critical error." -ForegroundColor Red
            # break
        }
    }
}
Write-Host "--- Phase 2 Finished ---"


# --- Final Summary ---

Write-Host " " # Spacer
Write-Host "--- Overall Summary ---" -ForegroundColor Cyan

# Report build status
if ($BuildSuccessful) {
    Write-Host "[Build Phase] Custom image '$CustomImageTag' built successfully." -ForegroundColor Green
} else {
    Write-Host "[Build Phase] Custom image '$CustomImageTag' build failed." -ForegroundColor Red
}

# Report pull status
if ($PullSuccessful) {
    Write-Host "[Pull Phase] All other required images pulled successfully or were already present." -ForegroundColor Green
} else {
    Write-Host "[Pull Phase] One or more other images failed to pull." -ForegroundColor Yellow
}

# Final outcome
if ($BuildSuccessful -and $PullSuccessful) {
    Write-Host "All Docker setup tasks completed successfully." -ForegroundColor Green
} else {
    Write-Host "One or more Docker setup tasks failed. Please review the output above." -ForegroundColor Red
}