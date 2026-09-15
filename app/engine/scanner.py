from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..api.schemas import WebhookSignal
from ..config import settings
from ..exchanges.base import ExchangeError, normalize_product_id
from ..strategies import SignalAggregator
from .executor import build_market_order, submit_market_order
from .notify import notify
from .portfolio import Portfolio
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


def scan_pairs() -> list[str]:
    """Configured scan universe as normalized product ids."""
    raw = getattr(settings, "scan_pairs", "") or AUTONOMOUS_PRODUCT_ID
    pairs = [normalize_product_id(p) for p in raw.split(",") if p.strip()]
    return pairs or [AUTONOMOUS_PRODUCT_ID]


_scan_in_progress = False
_last_trade_at: datetime | None = None
_last_trade_at_by_pair: dict[str, datetime] = {}
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


async def analyze_pair(
    exchange: Any,
    product_id: str,
    aggregator: SignalAggregator,
) -> dict[str, Any]:
    """Market data + aggregated verdict for one pair (no trading)."""
    normalized = normalize_product_id(product_id)
    try:
        market_data = exchange.get_market_snapshot(normalized)
        multi_timeframe_data = exchange.get_multi_timeframe_data(normalized)
    except Exception as exc:
        raise HTTPException(502, f"{exchange.name} market data error: {exc}")

    volatility = calculate_volatility(market_data)
    mtf_score = calculate_multi_timeframe_score(multi_timeframe_data)
    verdict = await aggregator.analyze(
        normalized, market_data, multi_timeframe_data)

    final_action = verdict["action"]
    final_confidence = verdict["confidence"]
    if mtf_score > 0.01 and final_action == "BUY":
        final_confidence = min(95, final_confidence + 10)
    elif mtf_score < -0.01 and final_action == "SELL":
        final_confidence = min(95, final_confidence + 10)
    elif (mtf_score > 0.01 and final_action == "SELL") or \
         (mtf_score < -0.01 and final_action == "BUY"):
        final_confidence = max(50, final_confidence - 10)

    return {
        "product_id": normalized,
        "pair": exchange.to_product_id(normalized),
        "action": final_action,
        "confidence": final_confidence,
        "rationale": verdict["rationale"],
        "strategy": verdict["strategy"],
        "multi_timeframe_score": mtf_score,
        "signals": verdict["signals"],
        "market_data": market_data,
        "volatility": volatility,
    }


async def execute_auto_trade(
    exchange: Any,
    product_id: str,
    quote_amount: Decimal,
    min_confidence: int,
    aggregator: SignalAggregator,
    store: Portfolio,
    quote_assets: tuple[str, ...] = ("ZUSD", "USD", "USDT", "ZUSDT"),
    base_assets: tuple[str, ...] = ("XXBT", "XBT", "BTC"),
    _analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One scan-and-trade step against `exchange` for `product_id`.

    Runs the strategy aggregator, applies multi-timeframe adjustment and
    risk limits, builds/submits the order, and persists the position.
    Pass `_analysis` to reuse a prior analyze_pair() result.
    """
    normalized = normalize_product_id(product_id)

    if min_confidence < 0 or min_confidence > 100:
        raise HTTPException(400, "min_confidence must be between 0 and 100")
    if quote_amount <= 0:
        raise HTTPException(400, "quote_amount must be greater than zero")

    if _analysis is not None and _analysis.get("product_id") == normalized:
        analysis = _analysis
        market_data = analysis["market_data"]
        volatility = analysis["volatility"]
        verdict = analysis
    else:
        analysis = await analyze_pair(exchange, normalized, aggregator)
        market_data = analysis["market_data"]
        volatility = analysis["volatility"]
        verdict = analysis

    base_response: dict[str, Any] = {
        "product_id": normalized,
        "pair": exchange.to_product_id(normalized),
        "action": analysis["action"],
        "confidence": analysis["confidence"],
        "rationale": analysis["rationale"],
        "order_ready": False,
        "submitted": False,
        "multi_timeframe_score": analysis["multi_timeframe_score"],
        "signals": analysis["signals"],
    }

    final_action = analysis["action"]
    final_confidence = analysis["confidence"]

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
        affordable = floor_to_increment(
            quote * Decimal("0.96"), Decimal("0.01"))
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

    if not settings.live_trading and not settings.paper_trading:
        return {"status": "dry_run", **ready_response, "live_trading": False}

    if settings.paper_trading and not settings.live_trading:
        # Paper ledger: full pipeline, simulated fill at estimated price.
        fill_price = float(price)
        store.open_position(
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=final_action,
            entry_price=fill_price,
            quote_amount=float(sized_quote),
            meta="paper",
        )
        store.record_trade(
            trade_id=f"PAPER-{signal.signal_id}",
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=final_action,
            price=fill_price,
            quote_amount=float(sized_quote),
            reason="paper_fill",
            strategy=verdict["strategy"],
        )
        return {
            "status": "paper_filled",
            **ready_response,
            "submitted": True,
            "live_trading": False,
            "paper": True,
            "fill_price": fill_price,
            "volatility": volatility,
            "dynamic_position_size": str(sized_quote),
        }

    result = submit_market_order(exchange, order)

    order_id = result.get("txid") or result.get("order_id")
    if order_id:
        store.open_position(
            position_id=signal.signal_id,
            exchange=exchange.name,
            product_id=normalized,
            action=final_action,
            entry_price=float(price),
            quote_amount=float(sized_quote),
            meta=f"order_id={order_id}",
        )
        confirmed = confirm_fill(exchange, str(order_id))
        notify("order_submitted", {
            "exchange": exchange.name, "product_id": normalized,
            "action": final_action, "quote": str(sized_quote),
            "order_id": str(order_id), "status": confirmed.get("status"),
        })

    return {
        "status": "submitted",
        **ready_response,
        "submitted": True,
        "live_trading": True,
        "exchange_response": result,
        "volatility": volatility,
        "dynamic_position_size": str(sized_quote),
    }


def confirm_fill(exchange: Any, order_id: str, attempts: int = 3,
                 delay_s: float = 1.0) -> dict[str, Any]:
    """Poll order status briefly; return last known status dict."""
    import time as _time
    last: dict[str, Any] = {"status": "unknown"}
    for _ in range(attempts):
        try:
            last = exchange.get_order_status(order_id)
        except ExchangeError as exc:
            last = {"status": "query_error", "error": str(exc)}
        if last.get("status") in {"filled", "cancelled", "rejected"}:
            break
        _time.sleep(delay_s)
    return last


def reconcile_exchange_state(exchange: Any, store: Portfolio,
                             quote_assets=("ZUSD", "USD", "USDT", "ZUSDT"),
                             ) -> dict[str, Any]:
    """Compare internal open exposure vs exchange balances; alert on drift."""
    try:
        balances = (exchange.get_account().get("balances") or {})
    except ExchangeError as exc:
        return {"ok": False, "error": str(exc)}
    usd = asset_balance(balances, *quote_assets)
    exposure = store.open_exposure(exchange.name)
    drift = {
        "exchange": exchange.name,
        "usd_balance": str(usd),
        "internal_open_exposure": str(exposure),
        "open_positions": len(store.get_open_positions(exchange.name)),
        "ok": True,
    }
    # Heuristic drift check: recorded exposure should not exceed cash+positions.
    if exposure > 0 and usd <= 0:
        drift["ok"] = False
        drift["reason"] = "open exposure with zero quote balance"
        notify("reconcile_drift", drift)
    return drift


_scan_counter = 0


async def run_autonomous_scan(
    exchange: Any,
    aggregator: SignalAggregator,
    store: Portfolio,
    product_id: str | None = None,
    quote_amount: Decimal = AUTONOMOUS_QUOTE_AMOUNT,
    min_confidence: int = AUTONOMOUS_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Scan every configured pair; trade the best candidate if any.

    When `product_id` is given, only that pair is scanned (legacy mode).
    """
    global _scan_in_progress, _last_trade_at, _last_scan_result, _scan_counter

    pairs = [normalize_product_id(product_id)] if product_id else scan_pairs()

    if _scan_in_progress:
        result = {
            "status": "skipped", "reason": "scan_in_progress",
            "product_id": pairs[0], "submitted": False,
        }
        _last_scan_result = result
        return result

    _scan_in_progress = True
    try:
        now = datetime.now(timezone.utc)

        # Kill-switch ladder: emergency_stop and ml_kill_switch block all
        # new orders before any market data or balance calls.
        if settings.emergency_stop:
            notify("emergency_stop", {"pairs": pairs})
            result = {
                "status": "blocked", "reason": "emergency_stop",
                "product_id": pairs[0], "submitted": False,
            }
            _last_scan_result = result
            return result
        if settings.ml_kill_switch:
            result = {
                "status": "blocked", "reason": "kill_switch",
                "product_id": pairs[0], "submitted": False,
            }
            _last_scan_result = result
            return result

        # Periodic reconciliation between internal ledger and exchange.
        _scan_counter += 1
        every = getattr(settings, "reconcile_every_scans", 20)
        if every > 0 and _scan_counter % every == 0:
            reconcile_exchange_state(exchange, store)

        cooldown_s = getattr(
            settings, "trade_cooldown_seconds",
            AUTONOMOUS_TRADE_COOLDOWN_SECONDS)

        # --- scan phase: analyze every pair, keep actionable candidates ---
        candidates: list[dict[str, Any]] = []
        scan_notes: list[dict[str, Any]] = []
        for pair in pairs:
            last = _last_trade_at_by_pair.get(pair)
            if last is not None:
                remaining = cooldown_s - (now - last).total_seconds()
                if remaining > 0:
                    scan_notes.append({
                        "product_id": pair, "status": "cooldown",
                        "remaining_seconds": int(remaining),
                    })
                    continue
            try:
                candidate = await analyze_pair(exchange, pair, aggregator)
            except HTTPException as exc:
                scan_notes.append({
                    "product_id": pair, "status": "error",
                    "detail": exc.detail,
                })
                continue
            except Exception as exc:
                scan_notes.append({
                    "product_id": pair, "status": "error",
                    "detail": str(exc),
                })
                continue
            if candidate["action"] != "HOLD":
                candidates.append(candidate)
            else:
                scan_notes.append({
                    "product_id": pair, "status": "hold",
                    "confidence": candidate["confidence"],
                })

        # --- pick the strongest candidate across the whole universe ---
        if not candidates:
            result = {
                "status": "hold",
                "reason": "no_actionable_pair",
                "scanned": len(pairs),
                "notes": scan_notes,
                "submitted": False,
            }
            _last_scan_result = result
            return result

        best = max(candidates, key=lambda c: c["confidence"])
        result = await execute_auto_trade(
            exchange, best["product_id"], quote_amount, min_confidence,
            aggregator, store, _analysis=best)
        result["scanned_pairs"] = len(pairs)
        result["candidates"] = [
            {"product_id": c["product_id"], "action": c["action"],
             "confidence": c["confidence"]}
            for c in sorted(candidates, key=lambda c: -c["confidence"])
        ]
        result["scan_notes"] = scan_notes

        if result.get("submitted"):
            _last_trade_at = now
            _last_trade_at_by_pair[best["product_id"]] = now
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
) -> None:
    """Continuously scan the configured pair universe (SCAN_PAIRS)."""
    seconds = getattr(settings, "scan_seconds", AUTONOMOUS_SCAN_SECONDS)
    await run_autonomous_scan(exchange, aggregator, store)
    while True:
        await asyncio.sleep(seconds)
        await run_autonomous_scan(exchange, aggregator, store)
