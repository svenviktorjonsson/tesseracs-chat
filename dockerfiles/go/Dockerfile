# dockerfiles/go/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends golang git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
