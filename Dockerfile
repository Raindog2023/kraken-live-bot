FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/bot \
    LOG_DIR=/var/lib/kraken-bot/logs \
    MODEL_DIR=/var/lib/kraken-bot/models \
    DATA_DIR=/var/lib/kraken-bot/data

RUN groupadd --system --gid 10001 bot && useradd --system --uid 10001 --gid bot --create-home --home-dir /home/bot bot \
    && mkdir -p /var/lib/kraken-bot/logs /var/lib/kraken-bot/models /var/lib/kraken-bot/data \
    && chown -R bot:bot /var/lib/kraken-bot /home/bot

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=bot:bot . .
RUN chmod +x /app/docker/entrypoint.sh /app/docker/healthcheck.py /app/scripts/*.sh
USER bot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["serve"]
