from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Action = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class Signal:
    """A single strategy's verdict for one product at one point in time."""

    strategy: str
    product_id: str
    action: Action
    confidence: int  # 0-100
    rationale: str
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Strategy(Protocol):
    """Pluggable analysis unit. Implementations must be side-effect free."""

    name: str
    weight: float          # aggregator weight
    min_confidence: int    # below this the strategy abstains (HOLD)
    enabled: bool          # can open trades when False -> shadow mode

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> Signal:
        """Evaluate current market state and return a Signal."""
        ...
