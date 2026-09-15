from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config import settings
from ..exchanges.base import ExchangeClient, ExchangeError, normalize_product_id
from .risk import decimal_from_value, floor_to_increment


def build_market_order(
    exchange: Any,
    signal: Any,
    product: dict[str, Any],
) -> dict[str, Any]:
    """Build a normalized market-order dict for the given exchange.

    `signal` carries signal_id/product_id/action/quote_amount; `product`
    is the snapshot product dict carrying price and size increments.
    """
    pair = exchange.to_product_id(signal.product_id)
    price = decimal_from_value(product.get("price"))

    if price <= 0:
        raise ExchangeError(
            f"{exchange.name} returned an invalid market price")

    quote_increment = decimal_from_value(
        product.get("quote_increment"), Decimal("0.01"))
    base_increment = decimal_from_value(
        product.get("base_increment"), Decimal("0.0001"))
    quote_amount = floor_to_increment(signal.quote_amount, quote_increment)

    if quote_amount <= 0:
        raise ExchangeError("Order amount is too small")

    if quote_amount > Decimal(str(settings.max_order_quote)):
        raise ExchangeError(
            f"Order exceeds MAX_ORDER_QUOTE={settings.max_order_quote}")

    normalized = normalize_product_id(signal.product_id)
    if signal.action == "BUY":
        return {
            "client_order_id": signal.signal_id,
            "product_id": normalized,
            "pair": pair,
            "side": "BUY",
            "quote_size": format(quote_amount, "f"),
            "estimated_price": format(price, "f"),
        }

    base_amount = floor_to_increment(quote_amount / price, base_increment)
    if base_amount <= 0:
        raise ExchangeError("Calculated sell amount is too small")

    return {
        "client_order_id": signal.signal_id,
        "product_id": normalized,
        "pair": pair,
        "side": "SELL",
        "base_size": format(base_amount, "f"),
        "estimated_price": format(price, "f"),
    }


def submit_market_order(exchange: Any, order: dict[str, Any]) -> dict[str, Any]:
    """Submit a normalized order dict through the exchange adapter."""
    side = str(order["side"]).lower()
    if side == "buy":
        return exchange.add_market_order(
            pair=order["pair"],
            side="buy",
            volume=order["quote_size"],
            volume_in_quote=True,
            userref=order["client_order_id"],
        )
    return exchange.add_market_order(
        pair=order["pair"],
        side="sell",
        volume=order["base_size"],
        volume_in_quote=False,
        userref=order["client_order_id"],
    )
