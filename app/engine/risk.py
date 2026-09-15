from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

from ..config import settings

MIN_LIVE_QUOTE = Decimal("5")
MAX_POSITION_SIZE = Decimal("200")
VOLATILITY_MULTIPLIER = 1.5


def decimal_from_value(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    steps = (value / increment).to_integral_value(rounding=ROUND_DOWN)
    return steps * increment


def asset_balance(balances: dict[str, Any], *names: str) -> Decimal:
    wanted = {name.upper() for name in names}
    total = Decimal("0")
    for key, value in balances.items():
        if str(key).upper() in wanted:
            total += decimal_from_value(value)
    return total


def calculate_dynamic_position_size(
    base_amount: Decimal,
    confidence: int,
    volatility: float = 1.0,
    max_position: Decimal = MAX_POSITION_SIZE,
) -> Decimal:
    """Scale quote size by confidence and volatility, capped at max_position."""
    if confidence < 50:
        return base_amount * Decimal("0.5")

    confidence_factor = Decimal(str(confidence)) / Decimal("100")
    volatility_factor = Decimal(str(min(volatility, VOLATILITY_MULTIPLIER)))

    return min(base_amount * confidence_factor * volatility_factor, max_position)


def calculate_volatility(market_data: dict[str, Any]) -> float:
    """0.5-2.0 normalized volatility from the last 10 candles."""
    candles = market_data.get("candles", [])
    if not candles or len(candles) < 10:
        return 1.0

    try:
        closes = [float(candle.get("close", 0)) for candle in candles[-10:]]
        if not closes or any(c <= 0 for c in closes):
            return 1.0

        returns = [abs((closes[i] - closes[i-1]) / closes[i-1]) for i in range(1, len(closes))]
        avg_volatility = sum(returns) / len(returns) if returns else 0.01
        return max(0.5, min(2.0, avg_volatility * 50))
    except (ZeroDivisionError, ValueError, TypeError):
        return 1.0


def calculate_multi_timeframe_score(multi_timeframe_data: dict[str, Any]) -> float:
    """Weighted combined momentum score across 5m/15m/1h."""
    scores = []

    for timeframe, candles in multi_timeframe_data.items():
        if not candles or len(candles) < 3:
            continue
        try:
            closes = [float(candle.get("close", 0)) for candle in candles[-3:]]
            if len(closes) < 2 or any(c <= 0 for c in closes):
                continue
            change = (closes[-1] - closes[0]) / closes[0]
            weight = {"5m": 2.0, "15m": 1.5}.get(timeframe, 1.0)
            scores.append(change * weight)
        except (ZeroDivisionError, ValueError, TypeError):
            continue

    return sum(scores) / len(scores) if scores else 0.0


def check_risk_limits(
    *,
    action: str,
    quote_amount: Decimal,
    daily_pnl: Decimal = Decimal("0"),
    open_exposure: Decimal = Decimal("0"),
) -> tuple[bool, str]:
    """Gate every order through configured risk limits.

    Returns (allowed, reason).
    """
    if settings.paused:
        return False, "bot_paused"

    if quote_amount <= 0:
        return False, "zero_quote_amount"

    if quote_amount > Decimal(str(settings.max_order_quote)):
        return False, f"exceeds_max_order_quote({settings.max_order_quote})"

    if open_exposure + quote_amount > Decimal(str(settings.ml_max_position_quote)):
        return False, f"exceeds_max_position_quote({settings.ml_max_position_quote})"

    if daily_pnl <= -Decimal(str(settings.ml_daily_loss_limit)):
        return False, f"daily_loss_limit_hit({settings.ml_daily_loss_limit})"

    return True, "ok"
