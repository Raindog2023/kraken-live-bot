from __future__ import annotations

from typing import Any

from ..godmod3_client import local_momentum_analysis
from .base import Signal


class MomentumStrategy:
    """Rule-based momentum strategy extracted from local_momentum_analysis.

    Scores directional momentum across 5m/15m/1h windows with volume
    confirmation. Deterministic — no external calls.
    """

    name = "momentum"
    weight = 1.0
    min_confidence = 65
    enabled = True

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> Signal:
        analysis = local_momentum_analysis(product_id, market_data)
        return Signal(
            strategy=self.name,
            product_id=product_id,
            action=analysis.action,
            confidence=analysis.confidence,
            rationale=analysis.rationale,
            meta={"source": "local_momentum"},
        )
