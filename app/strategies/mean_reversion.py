from __future__ import annotations

from typing import Any

from ..ml.features import calculate_bollinger_position, calculate_rsi
from .base import Signal


class MeanReversionStrategy:
    """Fade extreme Bollinger/RSI readings back toward the mean.

    BUY when price sits below the lower Bollinger band and RSI is oversold.
    SELL when price sits above the upper band and RSI is overbought.
    Deterministic — no external calls.
    """

    name = "mean_reversion"
    weight = 0.8
    min_confidence = 70
    enabled = True

    oversold_rsi = 30.0
    overbought_rsi = 70.0

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> Signal:
        candles = market_data.get("candles", [])
        if not isinstance(candles, list) or len(candles) < 26:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale="insufficient candles for mean reversion",
            )

        closes = [float(c.get("close", 0)) for c in candles if float(c.get("close", 0)) > 0]
        if len(closes) < 26:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=50,
                rationale="insufficient valid closes for mean reversion",
            )

        bollinger = calculate_bollinger_position(candles[-21:], period=20)
        rsi = calculate_rsi(closes, period=14)

        # bollinger_position: 0 = lower band, 1 = upper band
        if bollinger < 0.15 and rsi < self.oversold_rsi:
            strength = (0.15 - bollinger) + (self.oversold_rsi - rsi) / 100
            confidence = min(90, int(65 + strength * 100))
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="BUY",
                confidence=confidence,
                rationale=(
                    f"mean-reversion BUY: bollinger_pos={bollinger:.2f} "
                    f"rsi={rsi:.1f} (oversold)"
                ),
                meta={"bollinger_pos": bollinger, "rsi": rsi},
            )

        if bollinger > 0.85 and rsi > self.overbought_rsi:
            strength = (bollinger - 0.85) + (rsi - self.overbought_rsi) / 100
            confidence = min(90, int(65 + strength * 100))
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="SELL",
                confidence=confidence,
                rationale=(
                    f"mean-reversion SELL: bollinger_pos={bollinger:.2f} "
                    f"rsi={rsi:.1f} (overbought)"
                ),
                meta={"bollinger_pos": bollinger, "rsi": rsi},
            )

        return Signal(
            strategy=self.name,
            product_id=product_id,
            action="HOLD",
            confidence=50,
            rationale=(
                f"mean-reversion HOLD: bollinger_pos={bollinger:.2f} rsi={rsi:.1f}"
            ),
            meta={"bollinger_pos": bollinger, "rsi": rsi},
        )
