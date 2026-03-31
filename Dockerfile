FROM python:3.11-slim

# Security / ergonomics
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Create a non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY pipeline_templates /app/pipeline_templates

WORKDIR /app/backend

# Persist jobs/ bundles/ logs via volume (recommended in docker-compose)
RUN mkdir -p /app/backend/jobs && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# If behind a TLS termination proxy, you can enable proxy headers.
# FastAPI docs recommend --proxy-headers in that case.
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
