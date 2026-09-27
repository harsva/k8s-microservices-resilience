# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Run as non-root.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py .

ENV PORT=8080 \
    PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8080

# Use the app's own signal handling for graceful shutdown. gunicorn forwards
# SIGTERM; the worker drains in-flight requests before exiting.
CMD ["python", "worker.py"]
