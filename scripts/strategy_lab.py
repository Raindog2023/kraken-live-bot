"""Search for a long/flat signal with a positive edge *after* trading costs.

The production bot trades spot, so every candidate here is long-or-flat. A
signal is computed on a closed bar and filled at the next bar's open, which
keeps the walk honest: no candidate can act on a price it could not have seen.

Parameters are fitted on the first 70% of history and scored on the held-out
remainder, because any signal can be curve-fitted to look profitable in sample.

    python -m scripts.strategy_lab
    python -m scripts.strategy_lab --product ETH-USD --granularity 14400
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import product as iter_product

from scripts.market_data import Candle, load

TRAIN_FRACTION = 0.7


@dataclass(frozen=True)
class Result:
    name: str
    net_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    trades: int
    exposure_pct: float
    fees_paid_pct: float

    def row(self) -> str:
        return (
            f"{self.name:<34} {self.net_return_pct:>9.1f}% {self.cagr_pct:>8.1f}% "
            f"{self.max_drawdown_pct:>8.1f}% {self.sharpe:>7.2f} {self.trades:>7} "
            f"{self.exposure_pct:>8.0f}% {self.fees_paid_pct:>7.1f}%"
        )


HEADER = (
    f"{'strategy':<34} {'net':>10} {'cagr':>9} {'maxdd':>9} {'sharpe':>7} "
    f"{'trades':>7} {'expo':>9} {'fees':>8}"
)


def sma(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def rsi(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def rolling_max(values: Sequence[float], period: int) -> list[float | None]:
    return [None if i < period else max(values[i - period : i]) for i in range(len(values))]


def rolling_min(values: Sequence[float], period: int) -> list[float | None]:
    return [None if i < period else min(values[i - period : i]) for i in range(len(values))]


# --- signals: each returns a list of desired exposures (1.0 long, 0.0 flat) ---


def signal_buy_hold(candles: Sequence[Candle]) -> list[float]:
    return [1.0] * len(candles)


def signal_sma_cross(candles: Sequence[Candle], fast: int, slow: int) -> list[float]:
    closes = [c.close for c in candles]
    fast_ma, slow_ma = sma(closes, fast), sma(closes, slow)
    return [
        1.0 if f is not None and s is not None and f > s else 0.0
        for f, s in zip(fast_ma, slow_ma)
    ]


def signal_trend_filter(candles: Sequence[Candle], period: int) -> list[float]:
    closes = [c.close for c in candles]
    trend = sma(closes, period)
    return [1.0 if m is not None and c > m else 0.0 for c, m in zip(closes, trend)]


def signal_donchian(candles: Sequence[Candle], entry: int, exit_: int) -> list[float]:
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    upper, lower = rolling_max(highs, entry), rolling_min(lows, exit_)
    position = 0.0
    out: list[float] = []
    for i, close in enumerate(closes):
        if upper[i] is not None and close > upper[i]:
            position = 1.0
        elif lower[i] is not None and close < lower[i]:
            position = 0.0
        out.append(position)
    return out


def signal_tsmom(candles: Sequence[Candle], lookback: int, threshold_pct: float) -> list[float]:
    closes = [c.close for c in candles]
    out: list[float] = []
    for i, close in enumerate(closes):
        if i < lookback:
            out.append(0.0)
            continue
        change = (close / closes[i - lookback] - 1) * 100
        out.append(1.0 if change > threshold_pct else 0.0)
    return out


def signal_rsi_reversion(
    candles: Sequence[Candle], period: int, buy_below: float, exit_above: float
) -> list[float]:
    closes = [c.close for c in candles]
    values = rsi(closes, period)
    position = 0.0
    out: list[float] = []
    for value in values:
        if value is not None:
            if value < buy_below:
                position = 1.0
            elif value > exit_above:
                position = 0.0
        out.append(position)
    return out


def signal_dip_in_uptrend(
    candles: Sequence[Candle], trend: int, period: int, buy_below: float, exit_above: float
) -> list[float]:
    """Mean reversion, but only while the long trend is up."""
    closes = [c.close for c in candles]
    trend_ma = sma(closes, trend)
    values = rsi(closes, period)
    position = 0.0
    out: list[float] = []
    for i, value in enumerate(values):
        uptrend = trend_ma[i] is not None and closes[i] > trend_ma[i]
        if value is not None:
            if position == 0.0 and uptrend and value < buy_below:
                position = 1.0
            elif position == 1.0 and (value > exit_above or not uptrend):
                position = 0.0
        out.append(position)
    return out


# --- engine ---


@dataclass
class Candidate:
    name: str
    signal: object
    params: dict = field(default_factory=dict)

    def exposures(self, candles: Sequence[Candle]) -> list[float]:
        return self.signal(candles, **self.params)


def run(
    candles: Sequence[Candle],
    exposures: Sequence[float],
    name: str,
    cost_bps: float,
    bars_per_year: float,
) -> Result:
    """Fill on the next bar's open using the exposure decided on the last close."""
    equity = 1.0
    position = 0.0
    trades = 0
    fees = 0.0
    peak = 1.0
    max_dd = 0.0
    exposed_bars = 0
    returns: list[float] = []

    for i in range(1, len(candles) - 1):
        target = exposures[i - 1]
        fill = candles[i].open
        if target != position:
            equity *= 1 - abs(target - position) * cost_bps / 10_000
            fees += abs(target - position) * cost_bps / 10_000
            trades += 1
            position = target
        bar_return = candles[i + 1].open / fill - 1
        equity *= 1 + position * bar_return
        returns.append(position * bar_return)
        exposed_bars += position > 0
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)

    years = max(len(returns) / bars_per_year, 1e-9)
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = sum((r - mean) ** 2 for r in returns) / len(returns) if returns else 0.0
    stdev = math.sqrt(variance)
    sharpe = (mean / stdev) * math.sqrt(bars_per_year) if stdev else 0.0

    return Result(
        name=name,
        net_return_pct=(equity - 1) * 100,
        cagr_pct=(equity ** (1 / years) - 1) * 100,
        max_drawdown_pct=max_dd * 100,
        sharpe=sharpe,
        trades=trades,
        exposure_pct=100 * exposed_bars / len(returns) if returns else 0.0,
        fees_paid_pct=fees * 100,
    )


def grid(name: str, signal, **axes) -> Iterator[Candidate]:
    keys = list(axes)
    for combo in iter_product(*(axes[k] for k in keys)):
        params = dict(zip(keys, combo))
        if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
            continue
        label = ",".join(f"{k}={v}" for k, v in params.items())
        yield Candidate(f"{name}({label})", signal, params)


def candidates() -> list[Candidate]:
    out: list[Candidate] = [Candidate("buy_hold", signal_buy_hold)]
    out += grid(
        "sma_cross", signal_sma_cross, fast=[6, 12, 24, 48, 96], slow=[48, 96, 168, 336, 720]
    )
    out += grid("trend_filter", signal_trend_filter, period=[48, 96, 168, 336, 720])
    out += grid(
        "donchian", signal_donchian, entry=[24, 48, 96, 168, 336], exit_=[12, 24, 48, 96, 168]
    )
    out += grid(
        "tsmom", signal_tsmom, lookback=[12, 24, 48, 96, 168], threshold_pct=[0.0, 1.0, 3.0]
    )
    out += grid(
        "rsi_reversion",
        signal_rsi_reversion,
        period=[14, 24, 48],
        buy_below=[20.0, 25.0, 30.0, 35.0],
        exit_above=[50.0, 55.0, 65.0, 70.0],
    )
    out += grid(
        "dip_in_uptrend",
        signal_dip_in_uptrend,
        trend=[168, 336, 720],
        period=[14, 24],
        buy_below=[25.0, 30.0, 35.0, 40.0],
        exit_above=[50.0, 55.0, 60.0, 70.0],
    )
    return out


def median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if not ordered:
        return 0.0
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def family_report(
    candles: Sequence[Candle], cost_bps: float, bars_per_year: float, split: int
) -> None:
    """Score whole families, not their luckiest member.

    Picking the best parameter set in sample mostly measures how many parameter
    sets were tried. The median across a family is what a naive user actually
    gets, and it is the number that decides whether a family has any edge.
    """
    train, test = candles[:split], candles[split:]
    hold = run(test, signal_buy_hold(test), "buy_hold", cost_bps, bars_per_year)
    families: dict[str, list[tuple[Result, Result]]] = {}
    for candidate in candidates():
        family = candidate.name.split("(")[0]
        in_sample = run(train, candidate.exposures(train), candidate.name, cost_bps, bars_per_year)
        out_sample = run(test, candidate.exposures(test), candidate.name, cost_bps, bars_per_year)
        families.setdefault(family, []).append((in_sample, out_sample))

    print("\nFAMILY MEDIANS — every parameter set, not the luckiest one")
    print(
        f"{'family':<18} {'params':>7} {'train net':>11} {'test net':>10} "
        f"{'test sharpe':>12} {'test maxdd':>11} {'>hold':>7}"
    )
    print(f"{'buy_hold':<18} {1:>7} {'':>11} {hold.net_return_pct:>9.1f}% "
          f"{hold.sharpe:>12.2f} {hold.max_drawdown_pct:>10.1f}% {'':>7}")
    for family, pairs in sorted(families.items()):
        if family == "buy_hold":
            continue
        beat = sum(1 for _, out in pairs if out.net_return_pct > hold.net_return_pct)
        print(
            f"{family:<18} {len(pairs):>7} "
            f"{median([i.net_return_pct for i, _ in pairs]):>10.1f}% "
            f"{median([o.net_return_pct for _, o in pairs]):>9.1f}% "
            f"{median([o.sharpe for _, o in pairs]):>12.2f} "
            f"{median([o.max_drawdown_pct for _, o in pairs]):>10.1f}% "
            f"{100 * beat / len(pairs):>6.0f}%"
        )


def walk_forward(
    candles: Sequence[Candle],
    cost_bps: float,
    bars_per_year: float,
    lookback: int,
    step: int,
) -> None:
    """Refit periodically and trade the next window with those parameters.

    A single train/test split says as much about which window you drew as about
    the signal. Here every bar outside the first lookback is traded with
    parameters chosen only from data preceding it, so the concatenated segments
    form one equity curve that could actually have been achieved live.
    """
    all_candidates = candidates()
    picks: list[str] = []
    equity = 1.0
    hold_equity = 1.0
    segments = 0

    print(
        f"\nWALK-FORWARD — refit every {step} bars on the trailing {lookback}, "
        f"trade the next {step}"
    )
    print(f"{'window':<26} {'picked':<44} {'net':>8} {'hold':>8}")

    start = lookback
    while start + step < len(candles):
        train = candles[start - lookback : start]
        test = candles[start : start + step + 1]
        best = max(
            all_candidates,
            key=lambda c: run(train, c.exposures(train), c.name, cost_bps, bars_per_year).sharpe,
        )
        result = run(test, best.exposures(test), best.name, cost_bps, bars_per_year)
        hold = run(test, signal_buy_hold(test), "hold", cost_bps, bars_per_year)
        equity *= 1 + result.net_return_pct / 100
        hold_equity *= 1 + hold.net_return_pct / 100
        picks.append(best.name.split("(")[0])
        segments += 1
        window = f"{test[0].time.date()}->{test[-1].time.date()}"
        print(
            f"{window:<26} {best.name[:44]:<44} "
            f"{result.net_return_pct:>7.1f}% {hold.net_return_pct:>7.1f}%"
        )
        start += step

    print(
        f"\ncompounded over {segments} out-of-sample windows: "
        f"strategy {100 * (equity - 1):>7.1f}%   buy&hold {100 * (hold_equity - 1):>7.1f}%"
    )
    counts = {name: picks.count(name) for name in set(picks)}
    print("families chosen: " + ", ".join(f"{k}x{v}" for k, v in sorted(counts.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="BTC-USD")
    parser.add_argument("--granularity", type=int, default=3600)
    parser.add_argument("--days", type=int, default=900)
    parser.add_argument("--fee-bps", type=float, default=26.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--wf-lookback", type=int, default=365)
    parser.add_argument("--wf-step", type=int, default=90)
    args = parser.parse_args()

    cost_bps = args.fee_bps + args.slippage_bps
    candles = load(args.product, args.granularity, args.days)
    bars_per_year = 365 * 24 * 3600 / args.granularity
    split = int(len(candles) * TRAIN_FRACTION)
    train, test = candles[:split], candles[split:]

    print(
        f"{args.product} {args.granularity}s  {len(candles)} bars  "
        f"{candles[0].time.date()} -> {candles[-1].time.date()}  "
        f"cost {cost_bps:.0f}bps/side"
    )
    print(f"train {train[0].time.date()} -> {train[-1].time.date()}  ({len(train)} bars)")
    print(f" test {test[0].time.date()} -> {test[-1].time.date()}  ({len(test)} bars)\n")

    scored: list[tuple[Result, Candidate]] = []
    for candidate in candidates():
        exposures = candidate.exposures(train)
        scored.append(
            (run(train, exposures, candidate.name, cost_bps, bars_per_year), candidate)
        )
    scored.sort(key=lambda pair: pair[0].sharpe, reverse=True)

    hold_train = next(r for r, c in scored if c.name == "buy_hold")
    print("IN-SAMPLE (train) — ranked by Sharpe")
    print(HEADER)
    print(hold_train.row())
    for result, _ in scored[: args.top]:
        if result.name != "buy_hold":
            print(result.row())

    print("\nOUT-OF-SAMPLE (test) — same parameters, unseen data")
    print(HEADER)
    hold_test = run(test, signal_buy_hold(test), "buy_hold", cost_bps, bars_per_year)
    print(hold_test.row())
    survivors: list[Result] = []
    for _, candidate in scored[: args.top]:
        if candidate.name == "buy_hold":
            continue
        result = run(test, candidate.exposures(test), candidate.name, cost_bps, bars_per_year)
        survivors.append(result)
        print(result.row())

    family_report(candles, cost_bps, bars_per_year, split)

    if args.walk_forward:
        walk_forward(candles, cost_bps, bars_per_year, args.wf_lookback, args.wf_step)

    beat = [r for r in survivors if r.sharpe > hold_test.sharpe and r.net_return_pct > 0]
    print(
        f"\n{len(beat)}/{len(survivors)} top in-sample candidates kept a positive net return "
        f"AND beat buy&hold Sharpe out of sample."
    )
    for result in sorted(beat, key=lambda r: r.sharpe, reverse=True)[:5]:
        print(f"  {result.name}")


if __name__ == "__main__":
    main()
