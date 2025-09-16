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
