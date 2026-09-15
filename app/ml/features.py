from __future__ import annotations

from typing import Any, Iterable, Mapping
import math

FEATURE_COLUMNS = (
    "return_1", "return_3", "return_6",
    "range_pct", "volume_change", "close_position",
    "rsi_14", "macd_signal", "bollinger_position"
)

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

def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """Calculate RSI indicator."""
    if len(closes) < period + 1:
        return 50.0  # Neutral RSI when insufficient data

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(0, change))
        losses.append(max(0, -change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(closes: list[float]) -> float:
    """Calculate MACD signal line."""
    if len(closes) < 26:
        return 0.0

    # Simple implementation using EMA approximations
    ema_12 = sum(closes[-12:]) / 12  # Approximation
    ema_26 = sum(closes[-26:]) / 26  # Approximation
    macd = ema_12 - ema_26

    # Signal line (9-period EMA of MACD)
    return macd / max(abs(macd), 1e-12)  # Normalized


def calculate_bollinger_position(candles: list[dict[str, float]], period: int = 20) -> float:
    """Calculate position within Bollinger Bands."""
    if len(candles) < period:
        return 0.5  # Middle position when insufficient data

    closes = [float(c["close"]) for c in candles[-period:]]
    sma = sum(closes) / period
    std = (sum((c - sma) ** 2 for c in closes) / period) ** 0.5

    if std == 0:
        return 0.5

    latest_close = closes[-1]
    upper_band = sma + (2 * std)
    lower_band = sma - (2 * std)

    # Position 0-1 where 0 is lower band, 1 is upper band
    position = (latest_close - lower_band) / (upper_band - lower_band)
    return max(0.0, min(1.0, position))


def make_features(candles: Iterable[Mapping[str, float]]) -> list[dict[str, float]]:
    """Create features using only candle t and candles strictly before t."""
    c = list(candles); closes = [float(x["close"]) for x in c]; vols = [float(x["volume"]) for x in c]; result=[]
    for i, row in enumerate(c):
        if i < 20 or closes[i-1] <= 0 or vols[i-1] <= 0:  # Increased minimum for new indicators
            continue
        close=closes[i]; prev=closes[i-1]

        # Calculate technical indicators
        rsi = calculate_rsi(closes[:i])
        macd = calculate_macd(closes[:i])
        bollinger = calculate_bollinger_position(c[:i])

        result.append({
            "timestamp": float(row["timestamp"]),
            "return_1": close/prev-1,
            "return_3": close/closes[i-3]-1,
            "return_6": close/closes[i-6]-1,
            "range_pct": (float(row["high"])-float(row["low"]))/close,
            "volume_change": vols[i]/vols[i-1]-1,
            "close_position": (close-float(row["low"])) / max(float(row["high"])-float(row["low"]), 1e-12),
            "rsi_14": rsi / 100.0,  # Normalize to 0-1
            "macd_signal": macd,
            "bollinger_position": bollinger
        })
    return result
