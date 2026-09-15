from __future__ import annotations

from decimal import Decimal
from hmac import compare_digest
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from ..config import settings


class WebhookSignal(BaseModel):
    signal_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    action: Literal["BUY", "SELL"]
    quote_amount: Decimal = Field(gt=0)
    strategy: str = "MANUAL"


ACTION_ALIASES = {
    "BUY": "BUY", "LONG": "BUY", "ENTER_LONG": "BUY", "OPEN_LONG": "BUY",
    "BID": "BUY", "SELL": "SELL", "SHORT": "SELL", "ENTER_SHORT": "SELL",
    "EXIT_LONG": "SELL", "CLOSE_LONG": "SELL", "ASK": "SELL",
    "HOLD": "HOLD", "FLAT": "HOLD", "NEUTRAL": "HOLD", "NONE": "HOLD",
}

_PRODUCT_KEYS = (
    "product_id", "ticker", "symbol", "pair", "market", "instrument", "asset")
_ACTION_KEYS = ("action", "side", "signal", "direction", "order_action")
_AMOUNT_KEYS = (
    "quote_amount", "quote_size", "notional", "amount", "size", "quantity",
    "usd", "usd_amount")
_ID_KEYS = ("signal_id", "id", "alert_id", "uuid", "event_id")
_STRATEGY_KEYS = ("strategy", "strategy_name", "source", "bot")
_CONFIDENCE_KEYS = ("confidence", "score", "strength", "probability")
_NESTED_KEYS = ("data", "payload", "alert")


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


class ExternalSignal(BaseModel):
    """Inbound signal from a third-party provider (e.g. signal8).

    Field names vary between providers, so the payload is mapped from a
    set of common aliases onto the canonical WebhookSignal fields.
    `confidence` is normalized to 0-100 (0-1 inputs are scaled).
    """

    signal_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    action: Literal["BUY", "SELL", "HOLD"]
    quote_amount: Decimal | None = Field(default=None, gt=0)
    strategy: str = "SIGNAL8"
    confidence: int | None = Field(default=None, ge=0, le=100)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload

        flat: dict[str, Any] = {}
        for key in _NESTED_KEYS:
            nested = payload.get(key)
            if isinstance(nested, dict):
                flat.update({str(k).lower(): v for k, v in nested.items()})
        flat.update({str(k).lower(): v for k, v in payload.items()})

        raw_action = _first(flat, _ACTION_KEYS)
        action = ACTION_ALIASES.get(str(raw_action).strip().upper()) \
            if raw_action is not None else None
        if action is None:
            raise ValueError(f"unsupported action: {raw_action!r}")

        product = _first(flat, _PRODUCT_KEYS)
        if product is None:
            raise ValueError("missing product identifier")

        return {
            "signal_id": str(_first(flat, _ID_KEYS) or f"SIGNAL8-{uuid4()}"),
            "product_id": str(product),
            "action": action,
            "quote_amount": _first(flat, _AMOUNT_KEYS),
            "strategy": str(_first(flat, _STRATEGY_KEYS) or "SIGNAL8"),
            "confidence": _normalize_confidence(
                _first(flat, _CONFIDENCE_KEYS)),
            "raw": payload,
        }

    def to_webhook_signal(self, quote_amount: Decimal) -> WebhookSignal:
        return WebhookSignal(
            signal_id=self.signal_id,
            product_id=self.product_id,
            action=self.action,
            quote_amount=quote_amount,
            strategy=self.strategy,
        )


def _normalize_confidence(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 1:
        number *= 100
    return max(0, min(100, round(number)))


def require_webhook_secret(x_webhook_secret: str | None) -> None:
    secret = settings.webhook_secret or ""
    provided = x_webhook_secret or ""

    if not secret or not provided or not compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="invalid webhook secret")
