# build_all_images.ps1
# This script builds all the custom Docker images for the Tesseracs Chat project.

Write-Host "--- Starting Docker image build process ---" -ForegroundColor Green
Write-Host ""

# Helper function to run a build command and check for errors
function Build-DockerImage {
    param(
        [string]$TagName,
        [string]$DockerfilePath
    )
    
    if (-not (Test-Path $DockerfilePath)) {
        Write-Error "ERROR: Dockerfile not found at '$DockerfilePath'. Skipping."
        return
    }
    
    Write-Host "Building image: $TagName..." -ForegroundColor Cyan
    docker build -t $TagName -f $DockerfilePath .
    
    if (-not $?) {
        Write-Error "FATAL: Build failed for image '$TagName'. Aborting script."
        exit 1 # Exit the script immediately on failure
    }
    
    Write-Host "SUCCESS: Image '$TagName' built." -ForegroundColor Green
    Write-Host "" # Add a blank line for readability
}

# --- Build All Custom Images ---

# The versatile Python image
Build-DockerImage -TagName "python-runner-with-uv" -DockerfilePath "./dockerfiles/python/Dockerfile"

# C/C++ image (gcc + python)
Build-DockerImage -TagName "tesseracs-gcc" -DockerfilePath "./dockerfiles/gcc/Dockerfile"

# Node.js image (node + python)
Build-DockerImage -TagName "tesseracs-node" -DockerfilePath "./dockerfiles/node/Dockerfile"

# Java image (openjdk + python)
Build-DockerImage -TagName "tesseracs-java" -DockerfilePath "./dockerfiles/java/Dockerfile"

# Rust image (rust + python)
Build-DockerImage -TagName "tesseracs-rust" -DockerfilePath "./dockerfiles/rust/Dockerfile"

# Go image (golang + python)
Build-DockerImage -TagName "tesseracs-go" -DockerfilePath "./dockerfiles/go/Dockerfile"

# .NET/C# image (dotnet-sdk + python)
Build-DockerImage -TagName "tesseracs-dotnet" -DockerfilePath "./dockerfiles/dotnet/Dockerfile"

Write-Host "--- All Docker images built successfully! ---" -ForegroundColor Green