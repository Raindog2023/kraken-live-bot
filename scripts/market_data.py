"""Public OHLC history for research and backtesting.

Coinbase Exchange serves paged candles going back years, which is the only free
source here with enough history to hold out a test set. Kraken's public OHLC
endpoint only returns the most recent ~720 candles, so it is used for recent
data and as a fallback.

Downloads are cached under ``data/ohlc`` so repeated sweeps are offline.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "ohlc"
MAX_CANDLES_PER_REQUEST = 300


@dataclass(frozen=True)
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _get(url: str, attempts: int = 5) -> list:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "kraken-live-bot-research"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


def _cache_path(product: str, granularity: int, days: int) -> Path:
    return CACHE_DIR / f"{product.replace('/', '-')}_{granularity}s_{days}d.csv"


def _read_cache(path: Path, max_age_seconds: float) -> list[Candle] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > max_age_seconds:
        return None
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        Candle(
            time=datetime.fromisoformat(row["time"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for row in rows
    ]


def _write_cache(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow(
                [
                    candle.time.isoformat(),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                ]
            )


def coinbase_candles(product: str, granularity: int, days: int) -> list[Candle]:
    """Page backwards through Coinbase candles, newest request first."""
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_floor = end - timedelta(days=days)
    span = timedelta(seconds=granularity * MAX_CANDLES_PER_REQUEST)
    by_time: dict[datetime, Candle] = {}

    while end > start_floor:
        start = max(start_floor, end - span)
        url = (
            f"{COINBASE_URL.format(product=product)}"
            f"?granularity={granularity}&start={start.isoformat()}&end={end.isoformat()}"
        )
        rows = _get(url)
        if not rows:
            break
        for row in rows:
            stamp = datetime.fromtimestamp(row[0], tz=UTC)
            by_time[stamp] = Candle(
                time=stamp,
                low=float(row[1]),
                high=float(row[2]),
                open=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        end = start
        time.sleep(0.25)

    return [by_time[key] for key in sorted(by_time)]


def kraken_candles(pair: str = "XBTUSD", interval_minutes: int = 60) -> list[Candle]:
    payload = _get(f"{KRAKEN_URL}?pair={pair}&interval={interval_minutes}")
    if payload.get("error"):
        raise RuntimeError(f"kraken error: {payload['error']}")
    result = payload["result"]
    key = next(k for k in result if k != "last")
    return [
        Candle(
            time=datetime.fromtimestamp(row[0], tz=UTC),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[6]),
        )
        for row in result[key]
    ]


def load(
    product: str = "BTC-USD",
    granularity: int = 3600,
    days: int = 900,
    cache_seconds: float = 6 * 3600,
) -> list[Candle]:
    path = _cache_path(product, granularity, days)
    cached = _read_cache(path, cache_seconds)
    if cached:
        return cached
    candles = coinbase_candles(product, granularity, days)
    if len(candles) < 100:
        raise RuntimeError(f"only got {len(candles)} candles for {product}")
    _write_cache(path, candles)
    return candles


if __name__ == "__main__":
    series = load()
    print(f"{len(series)} candles {series[0].time.date()} -> {series[-1].time.date()}")
