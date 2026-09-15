"""REST polling fallback for the market-data ingest service.

Polls Kraken and Coinbase public REST endpoints on a fixed cadence and
publishes the same normalized tick/candle messages as the WS collector.
Use when WebSocket connectivity is unavailable or as a warm-standby feed.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from ws_collector import CANDLES_TOPIC, TICKS_TOPIC, publisher

POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "30"))
KRAKEN_PAIRS = os.environ.get("KRAKEN_PAIRS", "XBTUSD,ETHUSD").split(",")
COINBASE_PRODUCTS = os.environ.get(
    "COINBASE_PRODUCTS", "BTC-USD,ETH-USD").split(",")


async def poll_kraken(client: httpx.AsyncClient) -> None:
    for pair in KRAKEN_PAIRS:
        try:
            r = await client.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": pair}, timeout=10)
            result = r.json().get("result") or {}
            for key, t in result.items():
                publisher.publish(TICKS_TOPIC, {
                    "type": "tick", "exchange": "kraken",
                    "product_id": key, "event_ts": time.time(),
                    "price": str(t["c"][0]), "bid": str(t["b"][0]),
                    "ask": str(t["a"][0]),
                    "volume_24h": str(t["v"][1]),
                    "ingest_ts": time.time(),
                })
            r = await client.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": 5}, timeout=10)
            result = r.json().get("result") or {}
            for key, series in result.items():
                if key == "last" or not isinstance(series, list):
                    continue
                for row in series[-5:]:
                    publisher.publish(CANDLES_TOPIC, {
                        "type": "candle", "exchange": "kraken",
                        "product_id": key, "interval_sec": 300,
                        "event_ts": float(row[0]),
                        "open": row[1], "high": row[2], "low": row[3],
                        "close": row[4], "volume": row[6],
                        "ingest_ts": time.time(),
                    })
        except Exception as exc:
            print(f"[poll:kraken:{pair}] {exc}")


async def poll_coinbase(client: httpx.AsyncClient) -> None:
    for product in COINBASE_PRODUCTS:
        try:
            r = await client.get(
                f"https://api.coinbase.com/api/v3/brokerage/products/"
                f"{product}/ticker", timeout=10)
            t = r.json()
            publisher.publish(TICKS_TOPIC, {
                "type": "tick", "exchange": "coinbase",
                "product_id": product, "event_ts": time.time(),
                "price": str(t.get("price", 0)),
                "bid": str(t.get("best_bid", 0)),
                "ask": str(t.get("best_ask", 0)),
                "volume_24h": str(t.get("volume_24_h", 0)),
                "ingest_ts": time.time(),
            })
        except Exception as exc:
            print(f"[poll:coinbase:{product}] {exc}")


async def main() -> None:
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.gather(
                poll_kraken(client), poll_coinbase(client))
            await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
