# Trading Platform Architecture

Modular, pluggable crypto trading platform built on the original
kraken-live-bot: FastAPI service + autonomous scanner + optional GCP
market-data pipeline.

## Layout

```
app/
  main.py                  app factory + lifespan wiring only
  config.py                pydantic-settings env config
  exchanges/
    base.py                ExchangeClient protocol + normalized types
    kraken.py              Kraken REST adapter (HMAC-SHA512)
    coinbase.py            Coinbase Advanced Trade (CDP ES256 JWT)
  kraken_client.py         compat shim -> exchanges.kraken
  strategies/
    base.py                Strategy protocol + Signal
    momentum.py            multi-TF momentum + volume confirm (rule)
    mean_reversion.py      Bollinger/RSI fade (rule)
    llm.py                 multi-provider LLM analyzer wrapper
    ml.py                  walk-forward ensemble (BQ or local features)
    aggregator.py          weighted_vote / any / unanimous combiner
  godmod3_client.py        LLM provider chain + local momentum core
  engine/
    risk.py                sizing, increments, risk gates (order/pos/daily)
    portfolio.py           SQLite positions + trades + PnL (persistent)
    executor.py            normalized order build + submit
    scanner.py             multi-pair loop, SL/TP, reconcile, kill-switch
    signals.py             inbound external-signal execution (/webhook)
    notify.py              alert webhook (fills, breaches, drift)
  api/
    schemas.py             WebhookSignal/ExternalSignal + secret guard
    routes.py              all REST endpoints + dashboard
  ml/
    features.py            FEATURE_COLUMNS + indicators (single source)
    labels.py              forward-return labels
    model.py               ensemble pipeline + walk-forward + artifacts
    backtest.py            legacy curve + per-strategy engine
    bq_features.py         BigQuery feature source (optional)

ingest/
  ws_collector.py          Kraken WS v2 + Coinbase WS -> Pub/Sub
  rest_poller.py           REST polling fallback

pipelines/
  dataflow_streaming.py    Pub/Sub -> BigQuery streaming (Beam, Flex)

training/
  feature_jobs.py          BQ candles -> features (same math as bot)
  train.py                 walk-forward train -> GCS artifact

deploy/
  provision.sh             GCP project/APIs/SAs/PubSub/BQ/budget
  bigquery_schema.sql      ticks/candles/features/signals/orders DDL
  cloudbuild.yaml          Dataflow flex-template build
```

## Data flow

```
Kraken WS v2 ─┐
              ├─> ingest/ws_collector ─> Pub/Sub ─> Dataflow ─> BigQuery
Coinbase WS ──┘        (rest_poller fallback)         (market_raw/features)
                                                          │
                              training/feature_jobs <─────┘
                                   │ -> kraken_market.features
                              training/train -> GCS artifact
                                   │
bot scanner: market data ─> strategies (momentum/mr/llm/ml)
            -> aggregator -> risk gates -> executor -> exchange
            -> portfolio.db (positions/trades) -> dashboard/alerts
```

## Scan cycle (multi-pair)

Each `SCAN_SECONDS` tick:

1. Kill-switch ladder: `EMERGENCY_STOP` > `PAUSED` > `ML_KILL_SWITCH`.
2. Every `RECONCILE_EVERY_SCANS`, reconcile internal ledger vs exchange.
3. For each pair in `SCAN_PAIRS` (skipping per-pair cooldowns):
   fetch snapshot + multi-timeframe OHLC, run every strategy,
   aggregate into one verdict, apply MTF confidence adjustment.
4. Rank candidates; trade the single strongest above `min_confidence`.
5. Risk gates: order/position caps + daily-loss limit + balance check.
6. Execute: dry_run / paper fill / live order per mode flags.
7. Persist position + trade to SQLite; alert on fills/breaches/drift.

## Modes

| LIVE_TRADING | PAPER_TRADING | Behavior |
|---|---|---|
| false | false | dry_run — signal + order built, nothing submitted |
| false | true  | paper fill at estimated price, full ledger |
| true  | any   | real orders (live wins over paper) |

## Defaults (cautious)

- $50 base quote, $200 max order, $500 max position, $50 daily loss
- 2% stop-loss / 3% take-profit / 1.5% trailing stop
- Paper mode default ON; live trading requires explicit opt-in

## Key env vars

`ACTIVE_EXCHANGE` kraken|coinbase · `SCAN_PAIRS` csv universe ·
`SCAN_SECONDS` / `TRADE_COOLDOWN_SECONDS` · `LIVE_TRADING` /
`PAPER_TRADING` / `EMERGENCY_STOP` / `PAUSED` / `ML_KILL_SWITCH` ·
`ML_ENABLED` / `ML_CONFIDENCE_THRESHOLD` / `ML_MODEL_PATH` ·
`MAX_ORDER_QUOTE` / `ML_MAX_POSITION_QUOTE` / `ML_DAILY_LOSS_LIMIT` ·
`STOP_LOSS_PERCENTAGE` / `TAKE_PROFIT_PERCENTAGE` /
`TRAILING_STOP_PERCENTAGE` · `ALERT_WEBHOOK_URL` · `WEBHOOK_SECRET` ·
`WEBHOOK_SIGNALS_ENABLED` / `WEBHOOK_DEFAULT_QUOTE` /
`WEBHOOK_MIN_CONFIDENCE` ·
`GCP_PROJECT_ID` / `BQ_FEATURES_ENABLED` / `BQ_DATASET` /
`GCS_MODEL_URI` · `COINBASE_*` CDP credentials · `*_API_KEY` LLM keys.
