"""Supernova EMA strategy — Adaptive Session EMA (Pine Script v5 translation).

BUY  : in session (09:30–16:00 ET), volume > SMA(volume,34)*1.25, close > EMA(20)
SELL : drawdown > 8% from entry, outside session, volume filter failed, close < EMA(20)
HOLD  : everything else

Pure function — no I/O. Follows the kraken-live-bot Strategy protocol.
"""

from __future__ import annotations

from typing import Any

from .base import Signal


EMA_LEN = 20
SMA_VOL_N = 34
VOL_MULT = 1.25
MAX_DRAWDOWN = 0.08


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    ema = sum(values[:n]) / n
    k = 2.0 / (n + 1)
    for v in values[n:]:
        ema = v * k + ema * (1 - k)
    return ema


def _volume_ready(volume: float, volumes: list[float]) -> bool:
    vol_sma = _sma(volumes, SMA_VOL_N)
    if vol_sma is None:
        return False
    return volume > vol_sma * VOL_MULT


class SupernovaStrategy:
    """Rule-based EMA + volume + session strategy. Deterministic."""

    name = "supernova"
    weight = 1.0
    min_confidence = 65
    enabled = True

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> Signal:
        candles = market_data.get("candles", [])
        if not isinstance(candles, list) or len(candles) < EMA_LEN:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale=f"insufficient candles for EMA{EMA_LEN}",
            )

        closes = [float(c.get("close", 0)) for c in candles if float(c.get("close", 0)) > 0]
        volumes = [float(c.get("volume", 0)) for c in candles if float(c.get("volume", 0)) > 0]

        if len(closes) < EMA_LEN or len(volumes) < SMA_VOL_N:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale="insufficient valid OHLCV for supernova",
            )

        close = closes[-1]
        volume = volumes[-1]
        ema_val = _ema(closes, EMA_LEN)
        vol_ok = _volume_ready(volume, volumes)

        # Crypto runs 24/7 — session filter is for equities only.
        # We treat crypto as always in session.
        in_session = True

        # Check for existing position via market_data meta
        position = market_data.get("position")
        has_position = bool(position and position.get("qty", 0) > 0)

        # --- SELL logic ---
        if has_position:
            entry = position.get("entry_price", 0)
            dd = (entry - close) / entry if entry else 0.0
            if dd > MAX_DRAWDOWN:
                return Signal(
                    strategy=self.name,
                    product_id=product_id,
                    action="SELL",
                    confidence=85,
                    rationale=f"supernova SELL: drawdown {dd:.1%} > {MAX_DRAWDOWN:.0%}",
                    meta={"drawdown": dd, "close": close, "ema": ema_val},
                )
            if not vol_ok:
                return Signal(
                    strategy=self.name,
                    product_id=product_id,
                    action="SELL",
                    confidence=70,
                    rationale="supernova SELL: volume filter failed",
                    meta={"volume": volume, "close": close, "ema": ema_val},
                )
            if ema_val is not None and close < ema_val:
                return Signal(
                    strategy=self.name,
                    product_id=product_id,
                    action="SELL",
                    confidence=75,
                    rationale=f"supernova SELL: close {close:.2f} < EMA{EMA_LEN} {ema_val:.2f}",
                    meta={"close": close, "ema": ema_val},
                )
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale="supernova HOLD: position open, no exit trigger",
                meta={"close": close, "ema": ema_val},
            )

        # --- BUY logic (flat) ---
        if not vol_ok:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale="supernova HOLD: volume below filter",
                meta={"volume": volume, "close": close, "ema": ema_val},
            )
        if ema_val is None:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale=f"supernova HOLD: insufficient history for EMA{EMA_LEN}",
                meta={"close": close},
            )
        if close > ema_val:
            confidence = min(90, int(65 + (close - ema_val) / ema_val * 200))
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="BUY",
                confidence=confidence,
                rationale=f"supernova BUY: close {close:.2f} > EMA{EMA_LEN} {ema_val:.2f}, volume ready",
                meta={"close": close, "ema": ema_val, "volume": volume},
            )
        return Signal(
            strategy=self.name,
            product_id=product_id,
            action="HOLD",
            confidence=50,
            rationale=f"supernova HOLD: close {close:.2f} <= EMA{EMA_LEN} {ema_val:.2f}",
            meta={"close": close, "ema": ema_val},
        )