"""Market-data ingest: Kraken WS v2 + Coinbase WS -> Pub/Sub.

Publishes normalized candle and tick messages to Pub/Sub topics for the
Dataflow streaming pipeline. Runs as a standalone service (own container
or process next to the trading bot).

Env:
    GCP_PROJECT_ID            required
    PUBSUB_CANDLES_TOPIC      default "market-candles"
    PUBSUB_TICKS_TOPIC        default "market-ticks"
    KRAKEN_PAIRS              csv, default "BTC/USD,ETH/USD"
    COINBASE_PRODUCTS         csv, default "BTC-USD,ETH-USD"
    INGEST_EXCHANGES          csv subset of kraken,coinbase (default both)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import websockets

try:
    from google.cloud import pubsub_v1
except ImportError:  # local dry-run without google-cloud-pubsub
    pubsub_v1 = None

KRAKEN_WS_PUBLIC = "wss://ws.kraken.com/v2"
COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
CANDLES_TOPIC = os.environ.get("PUBSUB_CANDLES_TOPIC", "market-candles")
TICKS_TOPIC = os.environ.get("PUBSUB_TICKS_TOPIC", "market-ticks")
KRAKEN_PAIRS = os.environ.get("KRAKEN_PAIRS", "BTC/USD,ETH/USD").split(",")
COINBASE_PRODUCTS = os.environ.get(
    "COINBASE_PRODUCTS", "BTC-USD,ETH-USD").split(",")
INGEST_EXCHANGES = set(
    os.environ.get("INGEST_EXCHANGES", "kraken,coinbase").split(","))


class Publisher:
    """Thin Pub/Sub publisher; falls back to stdout when unavailable."""

    def __init__(self) -> None:
        self._publisher = None
        self._topics: dict[str, str] = {}
        if pubsub_v1 is not None and PROJECT_ID:
            self._publisher = pubsub_v1.PublisherClient()
            self._topics = {
                name: self._publisher.topic_path(PROJECT_ID, name)
                for name in (CANDLES_TOPIC, TICKS_TOPIC)
            }

    def publish(self, topic: str, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode()
        if self._publisher is None:
            print(f"[dryrun:{topic}] {payload.decode()}")
            return
        self._publisher.publish(self._topics[topic], payload)


publisher = Publisher()


def _candle_msg(exchange: str, product_id: str, interval_sec: int,
                c: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "candle",
        "exchange": exchange,
        "product_id": product_id,
        "interval_sec": interval_sec,
        "event_ts": float(c["start"]),
        "open": str(c["open"]),
        "high": str(c["high"]),
        "low": str(c["low"]),
        "close": str(c["close"]),
        "volume": str(c["volume"]),
        "ingest_ts": time.time(),
    }


async def kraken_ws_loop() -> None:
    """Kraken WS v2: subscribe ohlc(5m)+ticker for configured pairs."""
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                    KRAKEN_WS_PUBLIC, ping_interval=20) as ws:
                for channel, params in (
                    ("ohlc", {"symbol": KRAKEN_PAIRS, "interval": 5}),
                    ("ticker", {"symbol": KRAKEN_PAIRS}),
                ):
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "params": {"channel": channel, **params},
                    }))
                backoff = 1.0
                async for raw in ws:
                    msg = json.loads(raw)
                    channel = msg.get("channel")
                    if channel == "ohlc" and msg.get("type") in {
                            "snapshot", "update"}:
                        for c in msg.get("data", []):
                            publisher.publish(CANDLES_TOPIC, _candle_msg(
                                "kraken",
                                msg.get("pair") or c.get("symbol", ""),
                                int(c.get("interval", 5)) * 60,
                                {
                                    "start": c.get("interval_begin") or c.get("timestamp") or 0,
                                    "open": c["open"], "high": c["high"],
                                    "low": c["low"], "close": c["close"],
                                    "volume": c.get("volume", 0),
                                }))
                    elif channel == "ticker" and msg.get("data"):
                        d = msg["data"][0]
                        publisher.publish(TICKS_TOPIC, {
                            "type": "tick",
                            "exchange": "kraken",
                            "product_id": d.get("symbol", ""),
                            "event_ts": time.time(),
                            "price": str(d.get("last", 0)),
                            "bid": str(d.get("bid", 0)),
                            "ask": str(d.get("ask", 0)),
                            "volume_24h": str(d.get("volume", 0)),
                            "ingest_ts": time.time(),
                        })
        except Exception as exc:
            print(f"[kraken-ws] reconnect in {backoff}s: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


async def coinbase_ws_loop() -> None:
    """Coinbase Advanced Trade WS: candles + ticker channels."""
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                    COINBASE_WS, ping_interval=20) as ws:
                for channel in ("candles", "ticker", "heartbeats"):
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": COINBASE_PRODUCTS,
                        "channel": channel,
                    }))
                backoff = 1.0
                async for raw in ws:
                    msg = json.loads(raw)
                    channel = msg.get("channel")
                    for event in msg.get("events", []):
                        if channel == "candles":
                            for c in event.get("candles", []):
                                publisher.publish(CANDLES_TOPIC, _candle_msg(
                                    "coinbase", c.get("product_id", ""),
                                    int(c.get("granularity_sec", 300)),
                                    {
                                        "start": int(c["start"]),
                                        "open": c["open"], "high": c["high"],
                                        "low": c["low"], "close": c["close"],
                                        "volume": c.get("volume", 0),
                                    }))
                        elif channel == "ticker":
                            for t in event.get("tickers", []):
                                publisher.publish(TICKS_TOPIC, {
                                    "type": "tick",
                                    "exchange": "coinbase",
                                    "product_id": t.get("product_id", ""),
                                    "event_ts": time.time(),
                                    "price": str(t.get("price", 0)),
                                    "bid": str(t.get("best_bid", 0)),
                                    "ask": str(t.get("best_ask", 0)),
                                    "volume_24h": str(t.get("volume_24_h", 0)),
                                    "ingest_ts": time.time(),
                                })
        except Exception as exc:
            print(f"[coinbase-ws] reconnect in {backoff}s: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


async def main() -> None:
    loops = []
    if "kraken" in INGEST_EXCHANGES:
        loops.append(kraken_ws_loop())
    if "coinbase" in INGEST_EXCHANGES:
        loops.append(coinbase_ws_loop())
    if not loops:
        raise SystemExit("INGEST_EXCHANGES selects nothing")
    await asyncio.gather(*loops)


if __name__ == "__main__":
    asyncio.run(main())
