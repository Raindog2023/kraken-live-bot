from __future__ import annotations

from decimal import Decimal
from hmac import compare_digest
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..config import settings


class WebhookSignal(BaseModel):
    signal_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    action: Literal["BUY", "SELL"]
    quote_amount: Decimal = Field(gt=0)
    strategy: str = "MANUAL"


def require_webhook_secret(x_webhook_secret: str | None) -> None:
    secret = settings.webhook_secret or ""
    provided = x_webhook_secret or ""

    if not secret or not provided or not compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="invalid webhook secret")
