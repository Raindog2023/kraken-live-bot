from __future__ import annotations
from typing import Sequence

def backtest(prices: Sequence[float], signals: Sequence[int], *, fee_bps: float=40, slippage_bps: float=5) -> dict[str,float]:
    if len(prices)!=len(signals) or len(prices)<2: raise ValueError("prices and signals must have equal length >= 2")
    cost=(fee_bps+slippage_bps)/10000; equity=1.0; position=0; trades=0
    for i in range(1,len(prices)):
        target=max(-1,min(1,int(signals[i-1])))
        if target!=position:
            equity*=max(0.0,1-cost*abs(target-position)); trades+=1; position=target
        equity*=1+position*(float(prices[i])/float(prices[i-1])-1)
    return {"final_equity":equity,"return_pct":(equity-1)*100,"trades":float(trades),"fee_bps":fee_bps,"slippage_bps":slippage_bps}
