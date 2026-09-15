"""Execution path for signals posted by external providers.

Unlike the autonomous scanner, the trade direction comes from the caller;
the bot still owns sizing, kill switches, risk limits, and bookkeeping.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..api.schemas import ExternalSignal
from ..config import settings
from ..exchanges.base import ExchangeError, normalize_product_id
from .executor import build_market_order, submit_market_order
from .notify import notify
from .portfolio import Portfolio
from .risk import (
    MIN_LIVE_QUOTE,
    asset_balance,
    check_risk_limits,
    decimal_from_value,
    floor_to_increment,
)


def execute_external_signal(
    exchange: Any,
    signal: ExternalSignal,
    store: Portfolio,
    quote_assets: tuple[str, ...] = ("ZUSD", "USD", "USDT", "ZUSDT"),
) -> dict[str, Any]:
    """Validate, size, and execute one externally supplied signal."""
    normalized = normalize_product_id(signal.product_id)
    base_response: dict[str, Any] = {
        "signal_id": signal.signal_id,
        "product_id": normalized,
        "action": signal.action,
        "strategy": signal.strategy,
        "confidence": signal.confidence,
        "submitted": False,
    }

    if not settings.webhook_signals_enabled:
        return {"status": "disabled", **base_response}

    if signal.action == "HOLD":
        return {"status": "ignored", "reason": "hold_signal", **base_response}

    if settings.emergency_stop:
        notify("emergency_stop", {"source": signal.strategy,
                                  "product_id": normalized})
        return {"status": "blocked", "reason": "emergency_stop",
                **base_response}

    if settings.ml_kill_switch:
        return {"status": "blocked", "reason": "kill_switch", **base_response}

    if settings.paused:
        return {"status": "paused", **base_response}

    if store.get_position(signal.signal_id) is not None:
        return {"status": "duplicate", "reason": "signal_id_already_traded",
                **base_response}

    min_confidence = settings.webhook_min_confidence
    if signal.confidence is not None and signal.confidence < min_confidence:
        return {"status": "low_confidence", "minimum_confidence":
                min_confidence, **base_response}

    try:
        market_data = exchange.get_market_snapshot(normalized)
    except Exception as exc:
        raise ExchangeError(
            f"{exchange.name} market data error: {exc}") from exc

    product = market_data.get("product", {})
    price = decimal_from_value(product.get("price"))
    if price <= 0:
        raise ExchangeError(f"{exchange.name} returned an invalid price")

    requested = signal.quote_amount or Decimal(
        str(settings.webhook_default_quote))
    quote_increment = decimal_from_value(
        product.get("quote_increment"), Decimal("0.01"))
    sized_quote = floor_to_increment(
        min(requested, Decimal(str(settings.max_order_quote))),
        quote_increment).quantize(quote_increment)

    try:
        balances = (exchange.get_account().get("balances") or {})
    except ExchangeError:
        balances = {}

    if balances:
        if signal.action == "BUY":
            available = floor_to_increment(
                asset_balance(balances, *quote_assets) * Decimal("0.96"),
                Decimal("0.01"))
            status = "insufficient_funds"
        else:
            base_asset = normalized.split("-")[0]
            aliases = (base_asset, f"X{base_asset}", "XBT", "XXBT") \
                if base_asset == "BTC" else (base_asset, f"X{base_asset}")
            available = floor_to_increment(
                asset_balance(balances, *aliases) * price * Decimal("0.98"),
                Decimal("0.01"))
            status = "insufficient_position"
        sized_quote = min(sized_quote, available)
        if sized_quote < MIN_LIVE_QUOTE:
            return {"status": status, **base_response,
                    "available_quote": format(available, "f"),
                    "needed": format(MIN_LIVE_QUOTE, "f")}

    allowed, reason = check_risk_limits(
        action=signal.action,
        quote_amount=sized_quote,
        daily_pnl=store.daily_pnl(),
        open_exposure=store.open_exposure(exchange.name),
    )
    if not allowed:
        return {"status": "risk_blocked", "reason": reason, **base_response}

    order = build_market_order(
        exchange, signal.to_webhook_signal(sized_quote), product)
    ready_response = {
        **base_response,
        "order": order,
        "quote_amount": format(sized_quote, "f"),
    }

    if not settings.live_trading and not settings.paper_trading:
        return {"status": "dry_run", **ready_response, "live_trading": False}

    if not settings.live_trading:
        store.open_position(
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=signal.action,
            entry_price=float(price),
            quote_amount=float(sized_quote),
            meta=f"paper;source={signal.strategy}",
        )
        store.record_trade(
            trade_id=f"PAPER-{signal.signal_id}",
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=signal.action,
            price=float(price),
            quote_amount=float(sized_quote),
            reason="paper_fill",
            strategy=signal.strategy,
        )
        return {"status": "paper_filled", **ready_response, "submitted": True,
                "live_trading": False, "paper": True,
                "fill_price": float(price)}

    result = submit_market_order(exchange, order)
    order_id = result.get("txid") or result.get("order_id")
    if order_id:
        store.open_position(
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=signal.action,
            entry_price=float(price),
            quote_amount=float(sized_quote),
            meta=f"order_id={order_id};source={signal.strategy}",
        )
        notify("order_submitted", {
            "exchange": exchange.name, "product_id": normalized,
            "action": signal.action, "quote": str(sized_quote),
            "order_id": str(order_id), "source": signal.strategy,
        })

    return {"status": "submitted", **ready_response, "submitted": True,
            "live_trading": True, "exchange_response": result}
