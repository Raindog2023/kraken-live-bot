from __future__ import annotations

from typing import Any

from ..godmod3_client import Godmod3Error, godmod3_client
from .base import Signal


class LLMStrategy:
    """Wraps the multi-provider LLM analyzer as a pluggable strategy.

    Uses the configured provider chain (claude -> openai -> gemini ->
    perplexity -> openrouter) with local-momentum fallback.
    """

    name = "llm"
    weight = 1.5
    min_confidence = 70
    enabled = True

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> Signal:
        try:
            analysis = await godmod3_client.analyze(product_id, market_data)
        except Godmod3Error as exc:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=0,
                rationale=f"llm_unavailable: {exc}",
                meta={"error": str(exc)},
            )
        return Signal(
            strategy=self.name,
            product_id=product_id,
            action=analysis.action,
            confidence=analysis.confidence,
            rationale=analysis.rationale,
            meta={"provider": godmod3_client.last_provider},
        )
