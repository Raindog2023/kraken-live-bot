#!/bin/sh
set -eu
umask 077
mkdir -p "${LOG_DIR:-/var/lib/kraken-bot/logs}" "${MODEL_DIR:-/var/lib/kraken-bot/models}" "${DATA_DIR:-/var/lib/kraken-bot/data}"
case "${1:-serve}" in
  serve) exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" ;;
  train) shift; exec /app/scripts/train.sh "$@" ;;
  backtest) shift; exec /app/scripts/backtest.sh "$@" ;;
  health) exec python /app/docker/healthcheck.py ;;
  *) echo "Usage: serve|train <csv> <artifact>|backtest <csv>|health" >&2; exit 2 ;;
esac
