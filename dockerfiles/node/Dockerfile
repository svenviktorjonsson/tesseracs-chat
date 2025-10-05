# dockerfiles/node/Dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm curl coreutils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv
