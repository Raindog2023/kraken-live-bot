# Frontend builder stage (if needed in future)
# FROM dhi.io/node:22.23-alpine AS frontend-builder
# WORKDIR /frontend
# COPY frontend/package*.json ./
# RUN npm ci --only=production

# Backend build stage with development tools
FROM dhi.io/python:3.11.16-debian13-dev AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt --target /app/site-packages

# Intermediate stage to prepare application files with permissions
FROM dhi.io/python:3.11.16-debian13-dev AS preparer

ENV PYTHONPATH=/app/site-packages \
    PATH=/app/site-packages/bin:$PATH

WORKDIR /app

COPY --from=builder /app/site-packages /app/site-packages

# Copy application code
COPY . .

# Create directories and set permissions
RUN mkdir -p \
    /var/lib/kraken-bot/logs \
    /var/lib/kraken-bot/models \
    /var/lib/kraken-bot/data \
    && chown -R 65532:65532 /var/lib/kraken-bot /app \
    && chmod +x /app/docker/entrypoint.sh /app/docker/healthcheck.py \
    && find /app/scripts -type f -name '*.sh' -exec chmod +x {} + 2>/dev/null || true

# Runtime stage
FROM dhi.io/python:3.11-debian

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/nonroot \
    LOG_DIR=/var/lib/kraken-bot/logs \
    MODEL_DIR=/var/lib/kraken-bot/models \
    DATA_DIR=/var/lib/kraken-bot/data \
    PYTHONPATH=/app/site-packages

WORKDIR /app

# Copy prepared files from preparer stage
COPY --from=preparer --chown=65532:65532 /app /app
COPY --from=preparer /var/lib/kraken-bot /var/lib/kraken-bot

# DHI runs as nonroot (UID 65532) by default
USER 65532

EXPOSE 8000

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=20s \
    --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]

ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
