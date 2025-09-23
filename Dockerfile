FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /code

ENV VIRTUAL_ENV=/code/.venv
RUN uv venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY . .

RUN uv pip install --no-cache .

EXPOSE 8000