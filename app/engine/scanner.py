from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..config import settings
from ..exchanges.base import ExchangeClient, ExchangeError, normalize_product_id
from ..strategies import Signal, SignalAggregator
from .executor import build_market_order, submit_market_order
from .portfolio import Portfolio, portfolio
from .risk import (
    MIN_LIVE_QUOTE,
    asset_balance,
    calculate_dynamic_position_size,
    calculate_multi_timeframe_score,
    calculate_volatility,
    check_risk_limits,
    decimal_from_value,
    floor_to_increment,
)

AUTONOMOUS_PRODUCT_ID = "BTC-USD"
AUTONOMOUS_QUOTE_AMOUNT = Decimal("50")
AUTONOMOUS_MIN_CONFIDENCE = 75
AUTONOMOUS_SCAN_SECONDS = 30
AUTONOMOUS_TRADE_COOLDOWN_SECONDS = 60

_scan_in_progress = False
_last_trade_at: datetime | None = None
_last_scan_result: dict[str, Any] | None = {
    "status": "initializing",
    "reason": "awaiting_first_scan",
    "product_id": AUTONOMOUS_PRODUCT_ID,
    "submitted": False,
}


def get_scan_state() -> dict[str, Any]:
    return {
        "scan_in_progress": _scan_in_progress,
        "last_trade_at": (
            _last_trade_at.isoformat() if _last_trade_at is not None else None),
        "last_scan": _last_scan_result,
    }


def check_stop_loss_take_profit(
    product_id: str,
    current_price: float,
    store: Portfolio,
) -> list[dict[str, Any]]:
    """Open positions on product_id that hit stop-loss/take-profit/trailing."""
    positions_to_close = []

    for position in store.get_open_positions():
        if position.get("product_id") != product_id:
            continue

        entry_price = float(position.get("entry_price", 0))
        action = position.get("action")
        if entry_price <= 0:
            continue

        stop_loss_pct = settings.stop_loss_percentage / 100
        take_profit_pct = settings.take_profit_percentage / 100
        trailing_stop_pct = settings.trailing_stop_percentage / 100

        if action == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price
            if pnl_pct >= take_profit_pct:
                positions_to_close.append({
                    "position_id": position["position_id"],
                    "reason": "take_profit",
                    "pnl_pct": pnl_pct,
                    "action": "SELL",
                })
            elif pnl_pct <= -stop_loss_pct:
                positions_to_close.append({
                    "position_id": position["position_id"],
                    "reason": "stop_loss",
                    "pnl_pct": pnl_pct,
                    "action": "SELL",
                })
            elif pnl_pct > 0:
                trailing_stop_price = entry_price * (
                    1 + (pnl_pct - trailing_stop_pct))
                if current_price < trailing_stop_price:
                    positions_to_close.append({
                        "position_id": position["position_id"],
                        "reason": "trailing_stop",
                        "pnl_pct": pnl_pct,
                        "action": "SELL",
                    })
        elif action == "SELL":
            pnl_pct = (entry_price - current_price) / entry_price
            if pnl_pct >= take_profit_pct:
                positions_to_close.append({
                    "position_id": position["position_id"],
                    "reason": "take_profit",
                    "pnl_pct": pnl_pct,
                    "action": "BUY",
                })
            elif pnl_pct <= -stop_loss_pct:
                positions_to_close.append({
                    "position_id": position["position_id"],
                    "reason": "stop_loss",
                    "pnl_pct": pnl_pct,
                    "action": "BUY",
                })

    return positions_to_close


async def execute_auto_trade(
    exchange: Any,
    product_id: str,
    quote_amount: Decimal,
    min_confidence: int,
    aggregator: SignalAggregator,
    store: Portfolio,
    quote_assets: tuple[str, ...] = ("ZUSD", "USD", "USDT", "ZUSDT"),
    base_assets: tuple[str, ...] = ("XXBT", "XBT", "BTC"),
) -> dict[str, Any]:
    """One scan-and-trade step against `exchange` for `product_id`.

    Runs the strategy aggregator, applies multi-timeframe adjustment and
    risk limits, builds/submits the order, and persists the position.
    """
    normalized = normalize_product_id(product_id)

    if min_confidence < 0 or min_confidence > 100:
        raise HTTPException(400, "min_confidence must be between 0 and 100")
    if quote_amount <= 0:
        raise HTTPException(400, "quote_amount must be greater than zero")

    try:
        market_data = exchange.get_market_snapshot(normalized)
        multi_timeframe_data = exchange.get_multi_timeframe_data(normalized)
    except Exception as exc:
        raise HTTPException(502, f"{exchange.name} market data error: {exc}")

    volatility = calculate_volatility(market_data)
    mtf_score = calculate_multi_timeframe_score(multi_timeframe_data)

    verdict = await aggregator.analyze(
        normalized, market_data, multi_timeframe_data)

    base_response: dict[str, Any] = {
        "product_id": normalized,
        "pair": exchange.to_product_id(normalized),
        "action": verdict["action"],
        "confidence": verdict["confidence"],
        "rationale": verdict["rationale"],
        "order_ready": False,
        "submitted": False,
        "multi_timeframe_score": mtf_score,
        "signals": verdict["signals"],
    }

    final_action = verdict["action"]
    final_confidence = verdict["confidence"]

    # Multi-timeframe confidence adjustment on the aggregated verdict.
    if mtf_score > 0.01 and final_action == "BUY":
        final_confidence = min(95, final_confidence + 10)
    elif mtf_score < -0.01 and final_action == "SELL":
        final_confidence = min(95, final_confidence + 10)
    elif (mtf_score > 0.01 and final_action == "SELL") or \
         (mtf_score < -0.01 and final_action == "BUY"):
        final_confidence = max(50, final_confidence - 10)

    base_response.update(
        {"action": final_action, "confidence": final_confidence})

    if final_action == "HOLD":
        return {"status": "hold", **base_response}

    if final_confidence < min_confidence:
        return {
            "status": "low_confidence",
            **base_response,
            "minimum_confidence": min_confidence,
        }

    try:
        balances = (exchange.get_account().get("balances") or {})
    except ExchangeError as exc:
        raise HTTPException(
            502, f"{exchange.name} balance error: {exc}") from exc

    price = decimal_from_value(market_data.get("product", {}).get("price"))

    sized_quote = calculate_dynamic_position_size(
        quote_amount, final_confidence, volatility)

    if final_action == "BUY":
        quote = asset_balance(balances, *quote_assets)
        affordable = floor_to_increment(quote * Decimal("0.96"), Decimal("0.01"))
        sized_quote = min(sized_quote, affordable)
        if sized_quote < MIN_LIVE_QUOTE:
            return {
                "status": "insufficient_funds",
                **base_response,
                "usd_balance": format(quote, "f"),
                "needed": format(MIN_LIVE_QUOTE, "f"),
            }
    else:
        base = asset_balance(balances, *base_assets)
        sellable = floor_to_increment(
            base * price * Decimal("0.98"), Decimal("0.01"))
        sized_quote = min(sized_quote, sellable)
        if sized_quote < MIN_LIVE_QUOTE:
            return {
                "status": "insufficient_position",
                **base_response,
                "balance": format(base, "f"),
                "usd_value": format(sellable, "f"),
            }

    allowed, reason = check_risk_limits(
        action=final_action,
        quote_amount=sized_quote,
        daily_pnl=store.daily_pnl(),
        open_exposure=store.open_exposure(exchange.name),
    )
    if not allowed:
        return {"status": "risk_blocked", "reason": reason, **base_response}

    from ..api.schemas import WebhookSignal
    signal = WebhookSignal(
        signal_id=f"{exchange.name.upper()}-{uuid4()}",
        product_id=normalized,
        action=final_action,
        quote_amount=sized_quote,
        strategy="AUTO",
    )
    order = build_market_order(exchange, signal, market_data.get("product", {}))
    ready_response = {
        **base_response,
        "order_ready": True,
        "minimum_confidence": min_confidence,
        "order": order,
    }

    if settings.paused:
        return {"status": "paused", **ready_response}

    if not settings.live_trading:
        return {"status": "dry_run", **ready_response, "live_trading": False}

    result = submit_market_order(exchange, order)

    if result.get("txid") or result.get("order_id"):
        store.open_position(
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=final_action,
            entry_price=float(price),
            quote_amount=float(sized_quote),
        )

    return {
        "status": "submitted",
        **ready_response,
        "submitted": True,
        "live_trading": True,
        "exchange_response": result,
        "volatility": volatility,
        "dynamic_position_size": str(sized_quote),
    }


async def run_autonomous_scan(
    exchange: Any,
    aggregator: SignalAggregator,
    store: Portfolio,
    product_id: str = AUTONOMOUS_PRODUCT_ID,
    quote_amount: Decimal = AUTONOMOUS_QUOTE_AMOUNT,
    min_confidence: int = AUTONOMOUS_MIN_CONFIDENCE,
) -> dict[str, Any]:
    global _scan_in_progress, _last_trade_at, _last_scan_result

    if _scan_in_progress:
        result = {
            "status": "skipped", "reason": "scan_in_progress",
            "product_id": product_id, "submitted": False,
        }
        _last_scan_result = result
        return result

    _scan_in_progress = True
    try:
        now = datetime.now(timezone.utc)

        if _last_trade_at is not None:
            remaining = AUTONOMOUS_TRADE_COOLDOWN_SECONDS - (
                now - _last_trade_at).total_seconds()
            if remaining > 0:
                result = {
                    "status": "skipped", "reason": "trade_cooldown",
                    "product_id": product_id, "submitted": False,
                    "cooldown_remaining_seconds": int(remaining),
                }
                _last_scan_result = result
                return result

        result = await execute_auto_trade(
            exchange, product_id, quote_amount, min_confidence,
            aggregator, store)
        if result.get("submitted"):
            _last_trade_at = now
        _last_scan_result = result
        return result

    except HTTPException as exc:
        result = {
            "status": "error", "reason": "http_error",
            "product_id": product_id, "submitted": False,
            "detail": exc.detail, "status_code": exc.status_code,
        }
        _last_scan_result = result
        return result
    except Exception as exc:
        result = {
            "status": "error", "reason": "scan_failed",
            "product_id": product_id, "submitted": False,
            "detail": str(exc),
        }
        _last_scan_result = result
        return result
    finally:
        _scan_in_progress = False


async def autonomous_loop(
    exchange: Any,
    aggregator: SignalAggregator,
    store: Portfolio,
    product_id: str = AUTONOMOUS_PRODUCT_ID,
) -> None:
    await run_autonomous_scan(exchange, aggregator, store, product_id)
    while True:
        await asyncio.sleep(AUTONOMOUS_SCAN_SECONDS)
        await run_autonomous_scan(exchange, aggregator, store, product_id)
