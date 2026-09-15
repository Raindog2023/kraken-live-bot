"""Pluggable alerting — POST events to a configured webhook URL.

Events: order_filled, order_rejected, stop_loss, take_profit,
daily_loss_breach, reconcile_drift, scan_error, emergency_stop.

Set ALERT_WEBHOOK_URL to enable; failures never block trading flow.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("kraken-bot.notify")


def notify(event: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget alert to ALERT_WEBHOOK_URL (5s timeout, swallowed)."""
    url = getattr(settings, "alert_webhook_url", "")
    if not url:
        return
    body = {
        "event": event,
        "app": settings.app_name,
        "ts": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    try:
        httpx.post(url, json=body, timeout=5.0)
    except Exception as exc:
        log.warning("alert %s failed: %s", event, exc)
