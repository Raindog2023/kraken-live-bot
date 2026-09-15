"""Dataflow streaming pipeline: Pub/Sub market data -> BigQuery.

Reads normalized tick/candle JSON messages published by ingest/ and
writes them to kraken_market.ticks / kraken_market.candles, with a
dead-letter table for malformed payloads.

Launch (Flex Template preferred — see deploy/cloudbuild.yaml):
    python pipelines/dataflow_streaming.py \
        --runner DataflowRunner \
        --project $GCP_PROJECT_ID --region $REGION \
        --input_subscription projects/$GCP_PROJECT_ID/subscriptions/market-candles-dataflow \
        --ticks_subscription projects/$GCP_PROJECT_ID/subscriptions/market-ticks-dataflow \
        --dataset kraken_market \
        --temp_location gs://$BUCKET-dataflow-tmp/temp \
        --staging_location gs://$BUCKET-dataflow-tmp/staging \
        --service_account_email dataflow-worker@$GCP_PROJECT_ID.iam.gserviceaccount.com \
        --max_num_workers 2 --worker_machine_type n1-standard-1
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

CANDLE_SCHEMA = (
    "event_ts:TIMESTAMP,exchange:STRING,product_id:STRING,"
    "interval_sec:INTEGER,open:NUMERIC,high:NUMERIC,low:NUMERIC,"
    "close:NUMERIC,volume:NUMERIC,ingest_ts:TIMESTAMP"
)
TICK_SCHEMA = (
    "event_ts:TIMESTAMP,exchange:STRING,product_id:STRING,"
    "price:NUMERIC,bid:NUMERIC,ask:NUMERIC,volume_24h:NUMERIC,"
    "ingest_ts:TIMESTAMP"
)
DEADLETTER_SCHEMA = "event_ts:TIMESTAMP,stage:STRING,payload:STRING,error:STRING"


class ParseMessage(beam.DoFn):
    """Parse a normalized ingest message; route bad payloads to deadletter."""

    def process(self, element: bytes):
        import datetime
        try:
            msg = json.loads(element.decode("utf-8"))
            kind = msg.get("type")
            if kind == "candle":
                yield beam.pvalue.TaggedOutput("candle", {
                    "event_ts": _ts(msg["event_ts"]),
                    "exchange": msg["exchange"],
                    "product_id": msg["product_id"],
                    "interval_sec": int(msg["interval_sec"]),
                    "open": str(msg["open"]),
                    "high": str(msg["high"]),
                    "low": str(msg["low"]),
                    "close": str(msg["close"]),
                    "volume": str(msg["volume"]),
                    "ingest_ts": _ts(msg.get("ingest_ts")),
                })
            elif kind == "tick":
                yield beam.pvalue.TaggedOutput("tick", {
                    "event_ts": _ts(msg["event_ts"]),
                    "exchange": msg["exchange"],
                    "product_id": msg["product_id"],
                    "price": str(msg["price"]),
                    "bid": str(msg.get("bid")) if msg.get("bid") is not None else None,
                    "ask": str(msg.get("ask")) if msg.get("ask") is not None else None,
                    "volume_24h": (
                        str(msg.get("volume_24h"))
                        if msg.get("volume_24h") is not None else None),
                    "ingest_ts": _ts(msg.get("ingest_ts")),
                })
            else:
                yield beam.pvalue.TaggedOutput("dead", {
                    "event_ts": _ts(None),
                    "stage": "parse",
                    "payload": element.decode("utf-8")[:4096],
                    "error": f"unknown type {kind!r}",
                })
        except Exception as exc:
            yield beam.pvalue.TaggedOutput("dead", {
                "event_ts": _ts(None),
                "stage": "parse",
                "payload": element.decode("utf-8", "replace")[:4096],
                "error": str(exc)[:512],
            })


def _ts(value: Any) -> "datetime.datetime":
    import datetime
    if value is None:
        return datetime.datetime.now(datetime.timezone.utc)
    return datetime.datetime.fromtimestamp(
        float(value), tz=datetime.timezone.utc)


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="kraken_market")
    parser.add_argument("--input_subscription", required=True,
                        help="candles Pub/Sub subscription")
    parser.add_argument("--ticks_subscription", default=None,
                        help="ticks Pub/Sub subscription (optional second source)")
    parser.add_argument("--deadletter_table", default="pipeline_deadletter")
    known, pipeline_args = parser.parse_known_args(argv)

    table = lambda name: f"{known.project}:{known.dataset}.{name}"  # noqa: E731

    options = PipelineOptions(pipeline_args, streaming=True)
    with beam.Pipeline(options=options) as p:
        candles_in = p | "ReadCandles" >> beam.io.ReadFromPubSub(
            subscription=known.input_subscription)
        parsed = (
            candles_in
            | "ParseCandleMsgs" >> beam.ParDo(ParseMessage()).with_outputs(
                "candle", "tick", "dead"))

        parsed.candle | "WriteCandles" >> beam.io.WriteToBigQuery(
            table("candles"), schema=CANDLE_SCHEMA,
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND)

        tick_pcoll = parsed.tick
        if known.ticks_subscription:
            ticks_in = p | "ReadTicks" >> beam.io.ReadFromPubSub(
                subscription=known.ticks_subscription)
            parsed_ticks = (
                ticks_in
                | "ParseTickMsgs" >> beam.ParDo(ParseMessage()).with_outputs(
                    "candle", "tick", "dead"))
            tick_pcoll = (parsed.tick, parsed_ticks.tick) | "MergeTicks" >> beam.Flatten()
            parsed_ticks.candle | "WriteCandlesFromTicksSub" >> beam.io.WriteToBigQuery(
                table("candles"), schema=CANDLE_SCHEMA,
                write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND)
            dead_pcoll = (parsed.dead, parsed_ticks.dead) | "MergeDead" >> beam.Flatten()
        else:
            dead_pcoll = parsed.dead

        tick_pcoll | "WriteTicks" >> beam.io.WriteToBigQuery(
            table("ticks"), schema=TICK_SCHEMA,
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND)
        dead_pcoll | "WriteDeadletter" >> beam.io.WriteToBigQuery(
            table(known.deadletter_table), schema=DEADLETTER_SCHEMA,
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    run()
