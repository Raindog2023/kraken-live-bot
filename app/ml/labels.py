from __future__ import annotations
from typing import Mapping, Sequence

def forward_return_labels(candles: Sequence[Mapping[str, float]], *, horizon: int = 3, threshold: float = 0.001) -> list[int | None]:
    """Label candle t from close(t+horizon), never from data available at t."""
    if horizon < 1 or threshold < 0: raise ValueError("invalid horizon or threshold")
    closes=[float(x["close"]) for x in candles]; labels: list[int|None]=[]
    for i, close in enumerate(closes):
        if i+horizon >= len(closes) or close <= 0: labels.append(None); continue
        r=closes[i+horizon]/close-1
        labels.append(1 if r > threshold else (-1 if r < -threshold else 0))
    return labels
