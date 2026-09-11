"""Test the crypto edges that are actually documented, across a basket.

`strategy_lab.py` asked whether any single-asset technical signal beats holding
BTC; none did. The published crypto anomalies are portfolio-level, so this asks
the harder-to-fake question: given a basket of coins, does *relative* strength
(cross-sectional momentum) or risk scaling (volatility targeting) beat holding?

Everything is long/flat spot with no leverage, rebalanced on a fixed schedule,
charged on turnover. Weights are formed from data up to the rebalance date and
earn the following day's return, so no weight sees its own return.

    python -m scripts.portfolio_lab
    python -m scripts.portfolio_lab --rebalance-days 14 --top-k 2

Survivorship warning: the basket is coins listed on Coinbase *today*. Real-time
selection in 2022 would have included names that later died, so cross-sectional
results here are, if anything, flattered.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from scripts.market_data import load

BASKET = [
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "LINK-USD",
    "AVAX-USD",
    "DOGE-USD",
    "LTC-USD",
    "ADA-USD",
    "DOT-USD",
    "ATOM-USD",
]

Weights = dict[str, float]
Allocator = Callable[[list[str], dict[str, list[float]], int], Weights]


@dataclass(frozen=True)
class Panel:
    """Aligned daily closes: every asset shares one date index."""

    dates: list
    closes: dict[str, list[float]]

    def __len__(self) -> int:
        return len(self.dates)


def build_panel(symbols: Sequence[str], days: int) -> Panel:
    series = {}
    for symbol in symbols:
        try:
            series[symbol] = {c.time.date(): c.close for c in load(symbol, 86400, days)}
        except RuntimeError as exc:
            print(f"skipping {symbol}: {exc}")
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    return Panel(
        dates=common,
        closes={symbol: [series[symbol][d] for d in common] for symbol in series},
    )


@dataclass(frozen=True)
class Metrics:
    name: str
    net_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    turnover: float

    def row(self) -> str:
        return (
            f"{self.name:<40} {self.net_return_pct:>10.1f}% {self.cagr_pct:>8.1f}% "
            f"{self.max_drawdown_pct:>8.1f}% {self.sharpe:>7.2f} {self.turnover:>9.1f}x"
        )


HEADER = (
    f"{'strategy':<40} {'net':>11} {'cagr':>9} {'maxdd':>9} {'sharpe':>7} {'turnover':>10}"
)


def simulate(
    panel: Panel,
    allocator: Allocator,
    name: str,
    rebalance_days: int,
    cost_bps: float,
    warmup: int,
) -> Metrics:
    symbols = list(panel.closes)
    equity = 1.0
    held: Weights = {}
    peak = 1.0
    max_dd = 0.0
    turnover = 0.0
    returns: list[float] = []

    for day in range(warmup, len(panel) - 1):
        if (day - warmup) % rebalance_days == 0:
            target = allocator(symbols, panel.closes, day)
            traded = sum(
                abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held)
            )
            equity *= 1 - traded * cost_bps / 10_000
            turnover += traded
            held = target

        gain = 0.0
        for symbol, weight in held.items():
            prices = panel.closes[symbol]
            gain += weight * (prices[day + 1] / prices[day] - 1)
        equity *= 1 + gain
        returns.append(gain)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)

    years = max(len(returns) / 365, 1e-9)
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    stdev = math.sqrt(variance)

    return Metrics(
        name=name,
        net_return_pct=(equity - 1) * 100,
        cagr_pct=(equity ** (1 / years) - 1) * 100,
        max_drawdown_pct=max_dd * 100,
        sharpe=(mean / stdev) * math.sqrt(365) if stdev else 0.0,
        turnover=turnover,
    )


# --- allocators ---


def hold_one(symbol: str) -> Allocator:
    return lambda symbols, closes, day: {symbol: 1.0}


def equal_weight(symbols: Sequence[str], closes, day: int) -> Weights:
    return {s: 1 / len(symbols) for s in symbols}


def realized_vol(prices: Sequence[float], day: int, window: int) -> float:
    rets = [prices[i + 1] / prices[i] - 1 for i in range(day - window, day)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var * 365)


def cross_sectional_momentum(
    lookback: int, top_k: int, trend_filter: int | None = None, vol_weight: bool = False
) -> Allocator:
    """Hold the strongest ``top_k`` names, optionally only while trending up."""

    def allocate(symbols: Sequence[str], closes, day: int) -> Weights:
        scored = []
        for symbol in symbols:
            prices = closes[symbol]
            momentum = prices[day] / prices[day - lookback] - 1
            if trend_filter is not None:
                average = sum(prices[day - trend_filter : day]) / trend_filter
                if prices[day] < average:
                    continue
            scored.append((momentum, symbol))
        scored.sort(reverse=True)
        winners = [s for score, s in scored[:top_k] if score > 0]
        if not winners:
            return {}
        if not vol_weight:
            return {s: 1 / len(winners) for s in winners}
        inverse = {s: 1 / max(realized_vol(closes[s], day, 30), 1e-6) for s in winners}
        total = sum(inverse.values())
        # Cap at fully invested: spot only, no leverage.
        return {s: min(v / total, 1.0) for s, v in inverse.items()}

    return allocate


def vol_targeted(symbol: str, target_vol: float, window: int = 30) -> Allocator:
    def allocate(symbols: Sequence[str], closes, day: int) -> Weights:
        vol = realized_vol(closes[symbol], day, window)
        return {symbol: min(target_vol / vol, 1.0) if vol > 0 else 0.0}

    return allocate


def trend_following(symbol: str, period: int) -> Allocator:
    def allocate(symbols: Sequence[str], closes, day: int) -> Weights:
        prices = closes[symbol]
        average = sum(prices[day - period : day]) / period
        return {symbol: 1.0} if prices[day] > average else {}

    return allocate


def dca_report(panel: Panel, symbol: str, cost_bps: float) -> None:
    """Buy a fixed dollar amount weekly and never sell."""
    prices = panel.closes[symbol]
    units = spent = 0.0
    for day in range(0, len(prices), 7):
        spent += 1.0
        units += (1.0 * (1 - cost_bps / 10_000)) / prices[day]
    value = units * prices[-1]
    print(
        f"\nweekly DCA into {symbol}: invested {spent:.0f} units of cash -> "
        f"{value:.1f} ({100 * (value / spent - 1):+.1f}% on cash deployed, "
        f"lump sum would be {100 * (prices[-1] / prices[0] - 1):+.1f}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1600)
    parser.add_argument("--rebalance-days", type=int, default=7)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--fee-bps", type=float, default=26.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    cost_bps = args.fee_bps + args.slippage_bps
    panel = build_panel(BASKET, args.days)
    warmup = 200
    print(
        f"{len(panel.closes)} assets  {len(panel)} days  "
        f"{panel.dates[0]} -> {panel.dates[-1]}  cost {cost_bps:.0f}bps/side  "
        f"rebalance every {args.rebalance_days}d"
    )

    strategies: list[tuple[str, Allocator]] = [
        ("hold BTC", hold_one("BTC-USD")),
        ("hold equal-weight basket", equal_weight),
        ("BTC trend filter (100d)", trend_following("BTC-USD", 100)),
        ("BTC vol target 50%", vol_targeted("BTC-USD", 0.50)),
        ("BTC vol target 30%", vol_targeted("BTC-USD", 0.30)),
    ]
    for lookback in (30, 60, 90, 180):
        strategies.append(
            (
                f"xs momentum {lookback}d top{args.top_k}",
                cross_sectional_momentum(lookback, args.top_k),
            )
        )
        strategies.append(
            (
                f"xs momentum {lookback}d top{args.top_k} +trend",
                cross_sectional_momentum(lookback, args.top_k, trend_filter=100),
            )
        )

    halves = [
        ("FULL", 0, len(panel)),
        ("FIRST HALF", 0, len(panel) // 2),
        ("SECOND HALF (held out)", len(panel) // 2 - warmup, len(panel)),
    ]
    for label, start, end in halves:
        window = Panel(panel.dates[start:end], {s: p[start:end] for s, p in panel.closes.items()})
        print(f"\n{label}  {window.dates[warmup]} -> {window.dates[-1]}")
        print(HEADER)
        for name, allocator in strategies:
            print(
                simulate(
                    window, allocator, name, args.rebalance_days, cost_bps, warmup
                ).row()
            )

    dca_report(panel, "BTC-USD", cost_bps)


if __name__ == "__main__":
    main()
