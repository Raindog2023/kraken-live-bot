import random
import sys

sys.path.insert(0, ".")
from app.ml.backtest import backtest_strategy  # noqa: E402
from app.strategies import MeanReversionStrategy, MomentumStrategy  # noqa: E402

random.seed(7)
px = [100.0]
for _ in range(400):
    px.append(px[-1] * (1 + random.gauss(0.0002, 0.006)))
candles = [
    {"open": px[i], "high": px[i] * 1.002, "low": px[i] * 0.998,
     "close": px[i + 1], "volume": 10 + random.random() * 5,
     "start": i * 300}
    for i in range(len(px) - 1)
]

for s in [MomentumStrategy(), MeanReversionStrategy()]:
    r = backtest_strategy(s, candles, min_confidence=60)
    print(f"{r['strategy']:>15}: ret={r['return_pct']:+.2f}% "
          f"trades={r['trades']} win={r['win_rate']:.0f}% "
          f"dd={r['max_drawdown_pct']:.1f}% sharpe={r['sharpe']:.2f} "
          f"exp={r['exposure_pct']:.0f}%")
