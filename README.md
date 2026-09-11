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

## Does *any* signal work? (`scripts/strategy_lab.py`)

Before trusting a replacement signal, it has to survive data it was not fitted
on. `scripts/strategy_lab.py` pulls multi-year Coinbase candles, fits ~230
long/flat parameter sets across six classic families (MA cross, trend filter,
Donchian breakout, time-series momentum, RSI reversion, dip-buying in an
uptrend) on the first 70% of history and scores them on the rest.

```bash
python -m scripts.strategy_lab                                    # hourly, train/test split
python -m scripts.strategy_lab --granularity 86400 --walk-forward # daily, rolling refit
```

Results on BTC-USD (26 bps fee + 2 bps slippage per side):

- **No family beat buy & hold out of sample.** Walk-forward over 3 years,
  refitting every 90 days on the trailing year, compounded to **+57% versus
  +140% for holding**.
- **Fees are not the whole story.** Rerunning the same walk-forward at *zero*
  cost still returns +66% versus +148% — these signals have no predictive edge
  to spend on fees in the first place.
- **One effect did survive every window:** a long-horizon trend filter roughly
  halves drawdown (e.g. -28% versus -53% on daily bars). It buys protection,
  not profit.

That last point is the only research result wired into the live path, as
`TREND_FILTER_DAYS`: entries are blocked while price is below its N-day
average. Exits are never gated on it.

Treat this lab as the bar for any new idea. Anything that only looks good in
sample is noise.

## What actually made money (`scripts/portfolio_lab.py`)

Single-asset timing rules failing says nothing about the documented crypto
anomalies, which are portfolio-level. `scripts/portfolio_lab.py` takes a
10-coin basket (4.4 years of daily Coinbase closes) and tests cross-sectional
momentum, volatility targeting and trend following against simply holding,
rebalancing weekly and charging turnover.

```bash
python -m scripts.portfolio_lab
python -m scripts.portfolio_lab --rebalance-days 14 --top-k 2
```

Held-out second half (2024-07 → 2026-09), net of 28 bps per side:

| strategy | net | max drawdown |
| --- | --- | --- |
| **hold BTC** | **+34.8%** | -53.1% |
| BTC vol target 50% | +28.6% | -54.5% |
| BTC trend filter (100d) | -2.6% | -36.8% |
| xs momentum 90d, top 3 | -33.4% | -73.8% |
| hold equal-weight basket | -29.8% | -76.8% |

Rotating into the strongest alts was the worst thing tested, in both halves the
full-sample winner was BTC, and the trend filter's lower drawdown cost 37 points
of return. Nothing beat owning BTC.

So the mode this repo now ships for making money is the boring one:
`ACCUMULATE_ENABLED=true` buys `ACCUMULATE_QUOTE_AMOUNT` of spot every
`ACCUMULATE_INTERVAL_HOURS` and never sells. No LLM call, no ML, no momentum
gate, no stop-loss — every one of those was measured and every one of them
subtracted. `POST /accumulate` runs a single tick by hand.

It is not alpha, and it is not free of risk: BTC drew down 53% inside the test
window and can do it again. It is the only allocation here that survived data
it was not chosen on.

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
- `TREND_FILTER_ENABLED`, `TREND_FILTER_DAYS`: block entries while price is
  below its N-day average; protective exits ignore this filter
- `ACCUMULATE_ENABLED`, `ACCUMULATE_PRODUCT_ID`, `ACCUMULATE_QUOTE_AMOUNT`,
  `ACCUMULATE_INTERVAL_HOURS`: scheduled buy-and-hold accumulation; requires
  `LIVE_TRADING=true` to place real orders
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
