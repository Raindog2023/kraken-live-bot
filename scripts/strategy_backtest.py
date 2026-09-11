"""Replay the live signal logic on Kraken OHLC data, net of fees.

    python -m scripts.strategy_backtest --interval 5
    python -m scripts.strategy_backtest --interval 5 --sweep

`--sweep` compares edge hurdles, including `0` (the legacy always-trade
behaviour), so a strategy change can be judged against fee drag before it is
enabled live.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from typing import Any

from app.godmod3_client import summarize_candles
from app.strategy import CostModel, cost_aware_action, momentum_score

CANDLE_WINDOW = 60
SWEEP_MULTIPLES = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0)


def fetch_ohlc(pair: str, interval: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"pair": pair, "interval": interval})
    with urllib.request.urlopen(
        f"https://api.kraken.com/0/public/OHLC?{query}", timeout=30
    ) as response:
        payload = json.loads(response.read())
    if payload.get("error"):
        raise RuntimeError(f"Kraken error: {payload['error']}")
    series = next(
        value
        for key, value in payload["result"].items()
        if key != "last" and isinstance(value, list)
    )
    return [
        {
            "start": row[0],
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[6],
        }
        for row in series
    ]


def run(
    candles: list[dict[str, Any]],
    *,
    costs: CostModel,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> dict[str, Any]:
    cost = (costs.fee_bps + costs.slippage_bps) / 10_000
    equity = 1.0
    position = 0
    entry_price = 0.0
    trades = 0
    stops = 0
    targets = 0
    fees_paid = 0.0

    for index in range(CANDLE_WINDOW, len(candles) - 1):
        window = candles[index - CANDLE_WINDOW : index + 1]
        price = float(candles[index]["close"])
        action, _ = cost_aware_action(momentum_score(summarize_candles(window)), costs)
        target = position

        if position == 1 and entry_price > 0:
            change_pct = (price / entry_price - 1) * 100
            if change_pct <= -abs(stop_loss_pct):
                target, stops = 0, stops + 1
            elif change_pct >= abs(take_profit_pct):
                target, targets = 0, targets + 1

        if target == position:
            if action == "BUY":
                target = 1
            elif action == "SELL":
                target = 0

        if target != position:
            equity *= 1 - cost
            fees_paid += cost
            trades += 1
            position = target
            entry_price = price if position == 1 else 0.0

        equity *= 1 + position * (float(candles[index + 1]["close"]) / price - 1)

    first = float(candles[CANDLE_WINDOW]["close"])
    last = float(candles[-1]["close"])
    return {
        "min_edge_multiple": costs.min_edge_multiple,
        "required_edge_pct": round(costs.required_edge_pct, 4),
        "trades": trades,
        "stop_loss_exits": stops,
        "take_profit_exits": targets,
        "strategy_return_pct": round((equity - 1) * 100, 3),
        "buy_and_hold_return_pct": round((last / first - 1) * 100, 3),
        "total_fee_drag_pct": round(fees_paid * 100, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", default="XBTUSD")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--fee-bps", type=float, default=26.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--min-edge-multiple", type=float, default=1.0)
    parser.add_argument("--stop-loss-pct", type=float, default=1.5)
    parser.add_argument("--take-profit-pct", type=float, default=3.0)
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    candles = fetch_ohlc(args.pair, args.interval)
    multiples = SWEEP_MULTIPLES if args.sweep else (args.min_edge_multiple,)
    results = [
        run(
            candles,
            costs=CostModel(
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                min_edge_multiple=multiple,
            ),
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
        )
        for multiple in multiples
    ]
    print(
        json.dumps(
            {
                "pair": args.pair,
                "interval_minutes": args.interval,
                "candles": len(candles),
                "fee_bps": args.fee_bps,
                "slippage_bps": args.slippage_bps,
                "stop_loss_pct": args.stop_loss_pct,
                "take_profit_pct": args.take_profit_pct,
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
