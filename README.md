# Kraken Live Bot

Live XBTUSD scanner with an opt-in, leakage-safe ML signal path. The existing LLM/momentum analysis remains the fallback.

Dashboard: `/dashboard`

`/kraken/account`, `/analyze/{product_id}` and `/auto/{product_id}` require the
`X-Webhook-Secret` header.

## Measured performance (why autonomous trading is off)

`python -m scripts.strategy_backtest --interval 5 --sweep` replays the live
signal logic on real Kraken OHLC data, charging 26 bps taker fee + 2 bps
slippage per fill. On XBTUSD the momentum signal has no measurable edge; the
only variable that improves results is trading less:

| edge hurdle | trades (60h of 5m candles) | strategy | buy & hold |
| --- | --- | --- | --- |
| none (legacy behaviour) | 147 | -34.9% | -1.8% |
| 1x round-trip cost | 39 | -11.5% | -1.8% |
| 2x round-trip cost (default) | 13 | -5.7% | -1.8% |
| 4x round-trip cost | 0 | 0.0% | -1.8% |

Rerun the sweep before enabling `AUTONOMOUS_ENABLED` or `LIVE_TRADING`. If the
strategy does not beat buy & hold net of fees in the backtest, it will not beat
it live.

## Safe defaults
- `AUTONOMOUS_ENABLED=false`
- `ML_ENABLED=false`
- `ML_PAPER_MODE=true`
- `LIVE_TRADING=false`
- `ML_KILL_SWITCH=false`

ML training is offline only: normalize completed Kraken OHLCV candles, build causal price/volume features, train a chronological walk-forward logistic-regression pipeline, and save the artifact outside source control. No notebooks or datasets are included.

## Required environment variables
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `KRAKEN_BASE_URL`
- `ANTHROPIC_API_KEY` (or another configured analysis provider)
- `WEBHOOK_SECRET`

## ML and trading guardrails
- `AUTONOMOUS_ENABLED`, `AUTONOMOUS_SCAN_SECONDS`, `AUTONOMOUS_TRADE_COOLDOWN_SECONDS`
- `TAKER_FEE_BPS`, `SLIPPAGE_BPS`, `MIN_EDGE_MULTIPLE`: a BUY/SELL is only sent
  when momentum exceeds `MIN_EDGE_MULTIPLE` round trips of trading cost,
  whatever the LLM or ML model says
- `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT`: protective exits bypass analysis entirely
- `DAILY_LOSS_LIMIT`: realized losses past this halt trading until UTC midnight
- `ML_ENABLED`, `ML_PAPER_MODE`, `ML_KILL_SWITCH`
- `ML_MODEL_PATH`, `ML_CONFIDENCE_THRESHOLD`
- `ML_MAX_ORDER_QUOTE`, `ML_MAX_POSITION_QUOTE`, `ML_DAILY_LOSS_LIMIT`
- `LIVE_TRADING`, `PAUSED`

Set `LIVE_TRADING=true` only after validating paper/shadow behavior and the offline backtest. The ML path requires a fresh serialized artifact and completed candles; otherwise the existing LLM/momentum path is used.

## Docker deployment

The image includes the complete application and ML source, but never includes secrets, datasets, logs, or model artifacts. The Compose service runs as a non-root user, restarts unless stopped, and mounts persistent named volumes at `/var/lib/kraken-bot/{logs,models,data}`.

```bash
cp .env.example .env
# Edit .env locally; never commit it.
docker compose build
docker compose up -d

docker compose logs -f bot
docker compose stop
docker compose down                 # removes containers, keeps named volumes
docker compose run --rm bot health
docker compose run --rm bot train /var/lib/kraken-bot/data/training.csv /var/lib/kraken-bot/models/ml_model.joblib
docker compose run --rm bot backtest /var/lib/kraken-bot/data/backtest.csv
```

The container starts in paper/shadow mode by default (`LIVE_TRADING=false`, `ML_ENABLED=false`, `ML_PAPER_MODE=true`, `ML_KILL_SWITCH=false`). A compatible, fresh model artifact must exist in the models volume before enabling ML:

```bash
# First train and review the result/backtest; then set these in .env and recreate.
docker compose run --rm bot train /var/lib/kraken-bot/data/training.csv /var/lib/kraken-bot/models/ml_model.joblib
docker compose run --rm bot backtest /var/lib/kraken-bot/data/backtest.csv
docker compose up -d --force-recreate
```

`train` expects CSV columns named after `app.ml.features.FEATURE_COLUMNS` plus `label` (values `-1`, `0`, or `1`). `backtest` expects `price` and `signal` columns. Training data and artifacts remain outside source control. The `/health` endpoint is used for the Docker healthcheck.

For live trading, explicitly review limits, paper/shadow behavior, model freshness, API permissions, and exchange risk controls. Only then set `ML_ENABLED=true` and/or `LIVE_TRADING=true`; keep `ML_KILL_SWITCH=false` only when actively supervised. Docker Desktop/local Docker is not managed by this repository change.
