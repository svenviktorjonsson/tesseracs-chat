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
RUN python3 -m venv $VIRTUAL_ENV

# Add both .NET and the Python venv to the PATH
ENV PATH="/root/.dotnet:$VIRTUAL_ENV/bin:$PATH"

# Install uv into the venv
RUN pip install uv
