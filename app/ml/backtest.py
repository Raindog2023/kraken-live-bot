from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence


def backtest(prices: Sequence[float], signals: Sequence[int], *,
             fee_bps: float = 40, slippage_bps: float = 5) -> dict[str, float]:
    """Legacy equity-curve backtest over a price/signal series."""
    if len(prices) != len(signals) or len(prices) < 2:
        raise ValueError("prices and signals must have equal length >= 2")
    cost = (fee_bps + slippage_bps) / 10000
    equity = 1.0
    position = 0
    trades = 0
    for i in range(1, len(prices)):
        target = max(-1, min(1, int(signals[i - 1])))
        if target != position:
            equity *= max(0.0, 1 - cost * abs(target - position))
            trades += 1
            position = target
        equity *= 1 + position * (float(prices[i]) / float(prices[i - 1]) - 1)
    return {"final_equity": equity, "return_pct": (equity - 1) * 100,
            "trades": float(trades), "fee_bps": fee_bps,
            "slippage_bps": slippage_bps}


def _candles_to_market(candles: Sequence[Mapping[str, Any]],
                       end: int) -> dict[str, Any]:
    """Market-data snapshot shaped like the exchange snapshot."""
    window = list(candles[:end])
    return {
        "product": {"price": str(window[-1]["close"])},
        "candles": window,
    }


async def backtest_strategy_async(
    strategy: Any,
    candles: Sequence[Mapping[str, Any]],
    *,
    product_id: str = "BTC-USD",
    start: int = 30,
    fee_bps: float = 40,
    slippage_bps: float = 5,
    min_confidence: int = 70,
    quote_size: float = 50.0,
    warmup: int = 26,
) -> dict[str, Any]:
    """Replay candles through a strategy; simulate fills with fee+slippage.

    At each step t >= warmup the strategy sees candles[:t] only (no
    lookahead). A BUY opens a long position at close*(1+slip)+fee; a SELL
    (or HOLD after a position) closes at close*(1-slip)-fee. Reports
    Sharpe-ish stats, max drawdown, win rate, exposure.
    """
    cost = (fee_bps + slippage_bps) / 10000
    equity = 1.0
    position = 0.0          # +1 long / 0 flat (spot-only)
    entry_equity = 0.0
    trades: list[float] = []
    equity_curve = [1.0]
    bars_in_market = 0

    for t in range(warmup, len(candles)):
        md = _candles_to_market(candles, t)
        try:
            sig = await strategy.analyze(product_id, md, None)
        except Exception:
            continue

        px = float(candles[t]["close"])
        prev = float(candles[t - 1]["close"])

        if position != 0.0:
            equity *= 1 + position * (px / prev - 1)
            bars_in_market += 1

        if sig.action == "BUY" and sig.confidence >= min_confidence \
                and position == 0:
            equity *= 1 - cost
            position = 1.0
            entry_equity = equity
        elif sig.action == "SELL" and position != 0:
            equity *= 1 - cost
            trades.append(equity / entry_equity - 1)
            position = 0.0

        equity_curve.append(equity)

    if position != 0:
        trades.append(equity / entry_equity - 1)

    rets = [equity_curve[i] / equity_curve[i - 1] - 1
            for i in range(1, len(equity_curve))]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    sharpe = (mean / (var ** 0.5) * (len(rets) ** 0.5)) if var > 0 else 0.0

    peak, max_dd = 1.0, 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak)

    wins = [t for t in trades if t > 0]
    return {
        "strategy": getattr(strategy, "name", "?"),
        "final_equity": equity,
        "return_pct": (equity - 1) * 100,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
        "avg_trade_pct": (sum(trades) / len(trades) * 100) if trades else 0.0,
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "exposure_pct": bars_in_market / max(1, len(rets)) * 100,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "bars": len(candles),
    }


def backtest_strategy(strategy, candles, **kwargs) -> dict[str, Any]:
    """Sync wrapper around backtest_strategy_async."""
    return asyncio.run(backtest_strategy_async(strategy, candles, **kwargs))
