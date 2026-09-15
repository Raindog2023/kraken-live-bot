-- BigQuery schema for the kraken-bot market-data platform.
-- Dataset: kraken_market (created by deploy/provision.sh)
-- All tables are day-partitioned on event_ts and clustered by exchange+pair.

CREATE TABLE IF NOT EXISTS kraken_market.ticks (
    event_ts        TIMESTAMP NOT NULL,
    exchange        STRING NOT NULL,
    product_id      STRING NOT NULL,
    price           NUMERIC NOT NULL,
    bid             NUMERIC,
    ask             NUMERIC,
    volume_24h      NUMERIC,
    ingest_ts       TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_ts)
CLUSTER BY exchange, product_id;

CREATE TABLE IF NOT EXISTS kraken_market.candles (
    event_ts        TIMESTAMP NOT NULL,   -- candle open time
    exchange        STRING NOT NULL,
    product_id      STRING NOT NULL,
    interval_sec    INT64 NOT NULL,
    open            NUMERIC NOT NULL,
    high            NUMERIC NOT NULL,
    low             NUMERIC NOT NULL,
    close           NUMERIC NOT NULL,
    volume          NUMERIC NOT NULL,
    ingest_ts       TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_ts)
CLUSTER BY exchange, product_id, interval_sec;

CREATE TABLE IF NOT EXISTS kraken_market.features (
    feature_ts      TIMESTAMP NOT NULL,   -- candle open time the row predicts from
    exchange        STRING NOT NULL,
    product_id      STRING NOT NULL,
    return_1        FLOAT64,
    return_3        FLOAT64,
    return_6        FLOAT64,
    range_pct       FLOAT64,
    volume_change   FLOAT64,
    close_position  FLOAT64,
    rsi_14          FLOAT64,
    macd_signal     FLOAT64,
    bollinger_position FLOAT64,
    label_h3        INT64,                -- forward-return label, horizon 3
    computed_at     TIMESTAMP NOT NULL
)
PARTITION BY DATE(feature_ts)
CLUSTER BY exchange, product_id;

CREATE TABLE IF NOT EXISTS kraken_market.signals (
    event_ts        TIMESTAMP NOT NULL,
    exchange        STRING NOT NULL,
    product_id      STRING NOT NULL,
    strategy        STRING NOT NULL,
    action          STRING NOT NULL,
    confidence      INT64 NOT NULL,
    rationale       STRING,
    trade_id        STRING
)
PARTITION BY DATE(event_ts)
CLUSTER BY exchange, product_id, strategy;

CREATE TABLE IF NOT EXISTS kraken_market.orders (
    event_ts        TIMESTAMP NOT NULL,
    exchange        STRING NOT NULL,
    product_id      STRING NOT NULL,
    client_order_id STRING NOT NULL,
    side            STRING NOT NULL,
    quote_size      NUMERIC,
    base_size       NUMERIC,
    status          STRING,
    exchange_order_id STRING
)
PARTITION BY DATE(event_ts)
CLUSTER BY exchange, product_id;

CREATE TABLE IF NOT EXISTS kraken_market.pipeline_deadletter (
    event_ts        TIMESTAMP,
    stage           STRING,
    payload         STRING,
    error           STRING
)
PARTITION BY DATE(event_ts);
