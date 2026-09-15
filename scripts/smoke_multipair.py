"""Smoke: multi-pair scan loop over a fake exchange."""
import asyncio
import sys

sys.path.insert(0, ".")
from app.engine.scanner import run_autonomous_scan  # noqa: E402
from app.engine.portfolio import Portfolio  # noqa: E402
from app.strategies import (  # noqa: E402
    MeanReversionStrategy, MomentumStrategy, SignalAggregator)


class FakeExchange:
    name = "fake"

    @property
    def configured(self):
        return True

    def to_product_id(self, pair):
        return pair

    def get_market_snapshot(self, pid):
        base = {"BTC-USD": 50000, "ETH-USD": 3000, "SOL-USD": 150}[pid]
        # uptrend for BTC, downtrend for ETH, flat for SOL
        drift = {"BTC-USD": 0.4, "ETH-USD": -0.4, "SOL-USD": 0.001}[pid]
        candles = [{"open": base + drift * i, "high": base + drift * i + 2,
                    "low": base + drift * i - 2, "close": base + drift * (i + 1),
                    "volume": 10, "start": i * 300} for i in range(30)]
        return {"product": {"price": str(base), "quote_increment": "0.01",
                            "base_increment": "0.0001"},
                "candles": candles}

    def get_multi_timeframe_data(self, pid):
        snap = self.get_market_snapshot(pid)
        return {"5m": snap["candles"], "15m": snap["candles"],
                "1h": snap["candles"]}

    def get_account(self):
        return {"balances": {"USD": "1000", "BTC": "0"}}


async def main():
    import app.config as cfg
    cfg.settings.scan_pairs = "BTC-USD,ETH-USD,SOL-USD"
    cfg.settings.paper_trading = True
    cfg.settings.live_trading = False
    cfg.settings.ml_kill_switch = False
    cfg.settings.emergency_stop = False
    cfg.settings.paused = False
    store = Portfolio(":memory:")
    agg = SignalAggregator([MomentumStrategy(), MeanReversionStrategy()])
    for cycle in range(2):
        r = await run_autonomous_scan(FakeExchange(), agg, store,
                                      min_confidence=60)
        print(f"cycle {cycle}: status={r['status']} pair={r.get('product_id')} "
          f"action={r.get('action')} conf={r.get('confidence')} "
          f"candidates={r.get('candidates')}")
    print("positions:", store.get_open_positions())


asyncio.run(main())
