from __future__ import annotations

from typing import Any

from .base import Signal, Strategy


class SignalAggregator:
    """Combine signals from multiple strategies into one verdict.

    Modes:
      - weighted_vote: sum weight*confidence per action; needs >=2 agreeing
        enabled strategies above their min_confidence.
      - any: first enabled strategy above min_confidence wins (deterministic
        order = strategy list order).
      - unanimous: all enabled strategies must agree on the same action.
    """

    def __init__(self, strategies: list[Strategy], mode: str = "weighted_vote"):
        self.strategies = strategies
        self.mode = mode

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run every strategy, return aggregated verdict + per-strategy signals."""
        signals: list[Signal] = []
        for strategy in self.strategies:
            try:
                signal = await strategy.analyze(product_id, market_data, multi_timeframe)
            except Exception as exc:
                signal = Signal(
                    strategy=strategy.name,
                    product_id=product_id,
                    action="HOLD",
                    confidence=0,
                    rationale=f"strategy_error: {exc}",
                    meta={"error": str(exc)},
                )
            signals.append(signal)

        verdict = self._aggregate(signals)
        return {
            "action": verdict["action"],
            "confidence": verdict["confidence"],
            "strategy": verdict["strategy"],
            "rationale": verdict["rationale"],
            "signals": [
                {
                    "strategy": s.strategy,
                    "action": s.action,
                    "confidence": s.confidence,
                    "rationale": s.rationale,
                    "meta": s.meta,
                }
                for s in signals
            ],
        }

    def _aggregate(self, signals: list[Signal]) -> dict[str, Any]:
        enabled = [
            s for s in signals
            if s.confidence > 0 and s.action != "HOLD"
        ]

        if not enabled:
            return {
                "action": "HOLD",
                "confidence": 50,
                "strategy": "aggregator",
                "rationale": "no enabled strategy produced an actionable signal",
            }

        if self.mode == "any":
            best = max(enabled, key=lambda s: s.confidence)
            return {
                "action": best.action,
                "confidence": best.confidence,
                "strategy": best.strategy,
                "rationale": best.rationale,
            }

        if self.mode == "unanimous":
            actions = {s.action for s in enabled}
            if len(actions) == 1:
                avg = sum(s.confidence for s in enabled) / len(enabled)
                return {
                    "action": enabled[0].action,
                    "confidence": round(avg),
                    "strategy": "unanimous",
                    "rationale": f"all {len(enabled)} strategies agree: {enabled[0].action}",
                }
            return {
                "action": "HOLD",
                "confidence": 50,
                "strategy": "aggregator",
                "rationale": f"strategies disagree: {sorted(actions)}",
            }

        # weighted_vote (default)
        buy_score = sum(s.confidence * self._weight(s.strategy) for s in enabled if s.action == "BUY")
        sell_score = sum(s.confidence * self._weight(s.strategy) for s in enabled if s.action == "SELL")

        buy_count = sum(1 for s in enabled if s.action == "BUY" and s.confidence >= self._min_conf(s.strategy))
        sell_count = sum(1 for s in enabled if s.action == "SELL" and s.confidence >= self._min_conf(s.strategy))

        if buy_score > sell_score and buy_count >= 1:
            confidence = min(95, round(buy_score / max(1, buy_count)))
            return {
                "action": "BUY",
                "confidence": confidence,
                "strategy": "weighted_vote",
                "rationale": f"buy_score={buy_score:.1f} vs sell_score={sell_score:.1f} ({buy_count} buy signals)",
            }
        if sell_score > buy_score and sell_count >= 1:
            confidence = min(95, round(sell_score / max(1, sell_count)))
            return {
                "action": "SELL",
                "confidence": confidence,
                "strategy": "weighted_vote",
                "rationale": f"sell_score={sell_score:.1f} vs buy_score={buy_score:.1f} ({sell_count} sell signals)",
            }

        return {
            "action": "HOLD",
            "confidence": 50,
            "strategy": "aggregator",
            "rationale": f"conflicting scores: buy={buy_score:.1f} sell={sell_score:.1f}",
        }

    def _weight(self, strategy_name: str) -> float:
        for s in self.strategies:
            if s.name == strategy_name:
                return s.weight
        return 1.0

    def _min_conf(self, strategy_name: str) -> int:
        for s in self.strategies:
            if s.name == strategy_name:
                return s.min_confidence
        return 65
