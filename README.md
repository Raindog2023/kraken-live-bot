# Kraken Live Bot

Live XBTUSD scanner with an opt-in, leakage-safe ML signal path. The existing LLM/momentum analysis remains the fallback.

Dashboard: `/dashboard`

## Safe defaults
- `ML_ENABLED=false`
- `ML_PAPER_MODE=true`
- `LIVE_TRADING=false`
- `ML_KILL_SWITCH=false`

ML training is offline only: normalize completed Kraken OHLCV candles, build causal price/volume features, train a chronological walk-forward logistic-regression pipeline, and save the artifact outside source control. No notebooks or datasets are included.

## Required environment variables
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `KRAKEN_BASE_URL`
- `ANTHROPIC_API_KEY` (or another configured analysis provider)
- `WEBHOOK_SECRET`

## Inbound signals (signal8 and other providers)

`POST /webhook` executes a signal from an external provider. Send the
`X-Webhook-Secret` header matching `WEBHOOK_SECRET`:

```bash
curl -X POST https://<host>/webhook \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"signal_id":"s8-1","ticker":"BTCUSD","side":"long","size":75,"confidence":0.82}'
```

Field names are mapped from common provider aliases, so the payload does
not need renaming:

| Canonical | Accepted aliases |
|---|---|
| `signal_id` | `id`, `alert_id`, `uuid`, `event_id` (a UUID is generated if absent) |
| `product_id` | `ticker`, `symbol`, `pair`, `market`, `instrument`, `asset` |
| `action` | `side`, `signal`, `direction`, `order_action` — `long`/`buy`/`bid` → BUY, `short`/`sell`/`exit_long` → SELL, `flat`/`hold` → ignored |
| `quote_amount` | `quote_size`, `notional`, `amount`, `size`, `quantity`, `usd` (falls back to `WEBHOOK_DEFAULT_QUOTE`) |
| `confidence` | `score`, `strength`, `probability` (0-1 inputs are scaled to 0-100) |
| `strategy` | `strategy_name`, `source`, `bot` |

A payload wrapped in `data`, `payload`, or `alert` is unwrapped. The bot
still owns execution: kill switches (`EMERGENCY_STOP`, `ML_KILL_SWITCH`,
`PAUSED`), `MAX_ORDER_QUOTE` / position / daily-loss limits, balance
checks, and paper/live mode all apply. Repeat deliveries of the same
`signal_id` return `duplicate` without trading. When
`WEBHOOK_MIN_CONFIDENCE` is set, signals below it — and signals that omit
confidence — are skipped; a confidence that isn't a finite number is
rejected rather than treated as absent. Set `WEBHOOK_SIGNALS_ENABLED=false`
to reject inbound signals entirely.

## ML and trading guardrails
- `ML_ENABLED`, `ML_PAPER_MODE`, `ML_KILL_SWITCH`
- `ML_MODEL_PATH`, `ML_CONFIDENCE_THRESHOLD`
- `ML_MAX_ORDER_QUOTE`, `ML_MAX_POSITION_QUOTE`, `ML_DAILY_LOSS_LIMIT`
- `LIVE_TRADING`, `PAUSED`

Set `LIVE_TRADING=true` only after validating paper/shadow behavior and the offline backtest. The ML path requires a fresh serialized artifact and an `ohlcv` payload; otherwise the existing LLM/momentum path is used.

## Docker deployment

The build stages use [Docker Hardened Images](https://docs.docker.com/dhi/) from `dhi.io`, which is an authenticated registry: run `docker login dhi.io` with your Docker Hub credentials before building, locally and in CI.

The image includes the complete application and ML source, but never includes secrets, datasets, logs, or model artifacts. The Compose service runs as a non-root user, restarts unless stopped, and mounts persistent named volumes at `/var/lib/kraken-bot/{logs,models,data}`.

```bash
cp .env.example .env
# Edit .env locally; never commit it.
docker compose build
docker compose up -d

docker compose logs -f bot
docker compose stop
docker compose down                 # removes containers, keeps named volumes
docker compose exec bot python /app/docker/healthcheck.py
```

The runtime image starts uvicorn directly and contains no shell, so `train` and `backtest` run from the `-dev` build stage:

```bash
docker build --target preparer -t kraken-live-bot:dev .
docker run --rm -v kraken-live-bot_bot_data:/var/lib/kraken-bot/data -v kraken-live-bot_bot_models:/var/lib/kraken-bot/models \
  kraken-live-bot:dev /app/scripts/train.sh /var/lib/kraken-bot/data/training.csv /var/lib/kraken-bot/models/ml_model.joblib
docker run --rm -v kraken-live-bot_bot_data:/var/lib/kraken-bot/data \
  kraken-live-bot:dev /app/scripts/backtest.sh /var/lib/kraken-bot/data/backtest.csv
```

The container starts in paper/shadow mode by default (`LIVE_TRADING=false`, `ML_ENABLED=false`, `ML_PAPER_MODE=true`, `ML_KILL_SWITCH=false`). A compatible, fresh model artifact must exist in the models volume before enabling ML:

```bash
# First train and review the result/backtest with the commands above; then set
# these in .env and recreate.
docker compose up -d --force-recreate
```

`train` expects CSV columns named after `app.ml.features.FEATURE_COLUMNS` plus `label` (values `-1`, `0`, or `1`). `backtest` expects `price` and `signal` columns. Training data and artifacts remain outside source control. The `/health` endpoint is used for the Docker healthcheck.

For live trading, explicitly review limits, paper/shadow behavior, model freshness, API permissions, and exchange risk controls. Only then set `ML_ENABLED=true` and/or `LIVE_TRADING=true`; keep `ML_KILL_SWITCH=false` only when actively supervised. Docker Desktop/local Docker is not managed by this repository change.
