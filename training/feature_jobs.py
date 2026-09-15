"""Feature computation: BigQuery candles -> kraken_market.features.

Reads completed 5m candles from kraken_market.candles, computes the same
FEATURE_COLUMNS as app/ml/features.py (plus forward-return labels for
training rows), and appends rows to kraken_market.features.

Runs as a batch job (local, Cloud Run job, or Dataproc) on a schedule;
idempotent per (product_id, feature_ts) via MERGE-less delete+insert on
the processed window.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any

from google.cloud import bigquery

# Reuse the exact feature math the bot uses — single source of truth.
sys.path.insert(0, ".")
from app.ml.features import (  # noqa: E402
    FEATURE_COLUMNS,
    calculate_bollinger_position,
    calculate_macd,
    calculate_rsi,
)
from app.ml.labels import forward_return_labels  # noqa: E402


def fetch_candles(client: bigquery.Client, project: str, dataset: str,
                  product_id: str, exchange: str, interval_sec: int,
                  lookback_days: int) -> list[dict[str, Any]]:
    query = f"""
        SELECT event_ts, open, high, low, close, volume
        FROM `{project}.{dataset}.candles`
        WHERE exchange = @exchange
          AND product_id = @product
          AND interval_sec = @interval
          AND event_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(),
                                      INTERVAL @days DAY)
        ORDER BY event_ts
    """
    job = client.query(query, job_config=bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("exchange", "STRING", exchange),
            bigquery.ScalarQueryParameter("product", "STRING", product_id),
            bigquery.ScalarQueryParameter("interval", "INT64", interval_sec),
            bigquery.ScalarQueryParameter("days", "INT64", lookback_days),
        ]))
    return [
        {
            "timestamp": r["event_ts"].timestamp(),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"]),
        }
        for r in job.result()
    ]


def compute_feature_rows(candles: list[dict[str, float]],
                         label_horizon: int = 3,
                         label_threshold: float = 0.001
                         ) -> list[dict[str, Any]]:
    """Compute feature + label rows; identical math to app/ml/features.py."""
    closes = [c["close"] for c in candles]
    labels = forward_return_labels(
        candles, horizon=label_horizon, threshold=label_threshold)
    rows = []
    for i, candle in enumerate(candles):
        if i < 20 or closes[i - 1] <= 0:
            continue
        row = {
            "feature_ts": dt.datetime.fromtimestamp(
                candle["timestamp"], tz=dt.timezone.utc),
            "return_1": closes[i] / closes[i - 1] - 1,
            "return_3": closes[i] / closes[i - 3] - 1,
            "return_6": closes[i] / closes[i - 6] - 1,
            "range_pct": (candle["high"] - candle["low"]) / closes[i],
            "volume_change": (
                candle["volume"] / candles[i - 1]["volume"] - 1
                if candles[i - 1]["volume"] > 0 else 0.0),
            "close_position": (
                (closes[i] - candle["low"])
                / max(candle["high"] - candle["low"], 1e-12)),
            "rsi_14": calculate_rsi(closes[:i]) / 100.0,
            "macd_signal": calculate_macd(closes[:i]),
            "bollinger_position": calculate_bollinger_position(candles[:i]),
            "label_h3": labels[i],
            "computed_at": dt.datetime.now(dt.timezone.utc),
        }
        rows.append(row)
    return rows


def write_features(client: bigquery.Client, project: str, dataset: str,
                   exchange: str, product_id: str,
                   rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    table = f"{project}.{dataset}.features"
    out = [{**r,
            "feature_ts": r["feature_ts"].isoformat(),
            "computed_at": r["computed_at"].isoformat(),
            "exchange": exchange, "product_id": product_id}
           for r in rows]
    errors = client.insert_rows_json(table, out)
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors[:3]}")
    return len(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="kraken_market")
    parser.add_argument("--exchange", default="kraken")
    parser.add_argument("--products", default="BTC-USD,ETH-USD")
    parser.add_argument("--interval-sec", type=int, default=300)
    parser.add_argument("--lookback-days", type=int, default=30)
    args = parser.parse_args()

    client = bigquery.Client(project=args.project)
    for product in args.products.split(","):
        candles = fetch_candles(
            client, args.project, args.dataset, product,
            args.exchange, args.interval_sec, args.lookback_days)
        rows = compute_feature_rows(candles)
        written = write_features(
            client, args.project, args.dataset, args.exchange, product, rows)
        print(f"{product}: {len(candles)} candles -> {written} feature rows")


if __name__ == "__main__":
    main()
