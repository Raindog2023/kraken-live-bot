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

## ML and trading guardrails
- `ML_ENABLED`, `ML_PAPER_MODE`, `ML_KILL_SWITCH`
- `ML_MODEL_PATH`, `ML_CONFIDENCE_THRESHOLD`
- `ML_MAX_ORDER_QUOTE`, `ML_MAX_POSITION_QUOTE`, `ML_DAILY_LOSS_LIMIT`
- `LIVE_TRADING`, `PAUSED`

Set `LIVE_TRADING=true` only after validating paper/shadow behavior and the offline backtest. The ML path requires a fresh serialized artifact and an `ohlcv` payload; otherwise the existing LLM/momentum path is used.
