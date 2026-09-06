from __future__ import annotations

from typing import Any, Iterable, Mapping
import math

FEATURE_COLUMNS = ("return_1", "return_3", "return_6", "range_pct", "volume_change", "close_position")

def normalize_completed_ohlcv(rows: Iterable[Any], *, now: float | None = None, interval_seconds: int = 300) -> list[dict[str, float]]:
    """Normalize Kraken OHLC rows and exclude the current/incomplete candle."""
    import time
    cutoff = float(now if now is not None else time.time())
    out: list[dict[str, float]] = []
    for row in rows:
        if isinstance(row, Mapping):
            get = row.get
            values = [get(k) for k in ("time", "open", "high", "low", "close", "vwap", "volume", "count")]
        else:
            values = list(row)
        if len(values) < 7:
            continue
        try:
            ts, op, hi, lo, cl, vw, vol = map(float, values[:7])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (ts, op, hi, lo, cl, vw, vol)) or hi < lo or cl <= 0 or vol < 0:
            continue
        if ts + interval_seconds > cutoff:
            continue
        out.append({"timestamp": ts, "open": op, "high": hi, "low": lo, "close": cl, "vwap": vw, "volume": vol})
    return sorted({int(x["timestamp"]): x for x in out}.values(), key=lambda x: x["timestamp"])

def make_features(candles: Iterable[Mapping[str, float]]) -> list[dict[str, float]]:
    """Create features using only candle t and candles strictly before t."""
    c = list(candles); closes = [float(x["close"]) for x in c]; vols = [float(x["volume"]) for x in c]; result=[]
    for i, row in enumerate(c):
        if i < 6 or closes[i-1] <= 0 or vols[i-1] <= 0:
            continue
        close=closes[i]; prev=closes[i-1]
        result.append({"timestamp": float(row["timestamp"]), "return_1": close/prev-1, "return_3": close/closes[i-3]-1, "return_6": close/closes[i-6]-1, "range_pct": (float(row["high"])-float(row["low"]))/close, "volume_change": vols[i]/vols[i-1]-1, "close_position": (close-float(row["low"])) / max(float(row["high"])-float(row["low"]), 1e-12)})
    return result
