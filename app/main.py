from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from hmac import compare_digest
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import settings
from .godmod3_client import Godmod3Analysis, Godmod3Error, godmod3_client
from .kraken_client import KrakenError, kraken_client, to_kraken_pair


CODE_VERSION = "2.7.2-live-ai"
AUTONOMOUS_ENABLED = True
AUTONOMOUS_PRODUCT_ID = "BTC-USD"
AUTONOMOUS_QUOTE_AMOUNT = Decimal("25")
AUTONOMOUS_MIN_CONFIDENCE = 50
AUTONOMOUS_SCAN_SECONDS = 60
AUTONOMOUS_TRADE_COOLDOWN_SECONDS = 180
MIN_LIVE_QUOTE = Decimal("5")

_scan_in_progress = False
_last_trade_at: datetime | None = None
_last_scan_result: dict[str, Any] | None = None


async def _autonomous_loop() -> None:
    await run_autonomous_scan()

    while True:
        await asyncio.sleep(AUTONOMOUS_SCAN_SECONDS)
        await run_autonomous_scan()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_autonomous_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=settings.app_name,
    version="2.7.2-live-ai",
    lifespan=lifespan,
)


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
        raise HTTPException(
            status_code=401,
            detail="invalid webhook secret",
        )


def normalize_product_id(product_id: str) -> str:
    value = product_id.strip().upper()

    if "-" not in value and value.endswith("USD"):
        value = f"{value[:-3]}-USD"

    return value


def decimal_from_value(
    value: Any,
    default: Decimal = Decimal("0"),
) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def kraken_asset_balance(balances: dict[str, Any], *names: str) -> Decimal:
    wanted = {name.upper() for name in names}
    total = Decimal("0")
    for key, value in balances.items():
        if str(key).upper() in wanted:
            total += decimal_from_value(value)
    return total


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    steps = (value / increment).to_integral_value(rounding=ROUND_DOWN)
    return steps * increment


def build_market_order(
    signal: WebhookSignal,
    product: dict[str, Any],
) -> dict[str, Any]:
    product_id = normalize_product_id(signal.product_id)
    pair = to_kraken_pair(product_id)
    price = decimal_from_value(product.get("price"))

    if price <= 0:
        raise HTTPException(
            status_code=400,
            detail="Kraken returned an invalid market price",
        )

    quote_increment = decimal_from_value(
        product.get("quote_increment"),
        Decimal("0.01"),
    )
    base_increment = decimal_from_value(
        product.get("base_increment"),
        Decimal("0.0001"),
    )
    quote_amount = floor_to_increment(signal.quote_amount, quote_increment)

    if quote_amount <= 0:
        raise HTTPException(status_code=400, detail="Order amount is too small")

    if quote_amount > Decimal(str(settings.max_order_quote)):
        raise HTTPException(
            status_code=400,
            detail=f"Order exceeds MAX_ORDER_QUOTE={settings.max_order_quote}",
        )

    if signal.action == "BUY":
        return {
            "client_order_id": signal.signal_id,
            "product_id": product_id,
            "pair": pair,
            "side": "BUY",
            "quote_size": format(quote_amount, "f"),
            "estimated_price": format(price, "f"),
        }

    base_amount = floor_to_increment(quote_amount / price, base_increment)
    if base_amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Calculated sell amount is too small",
        )

    return {
        "client_order_id": signal.signal_id,
        "product_id": product_id,
        "pair": pair,
        "side": "SELL",
        "base_size": format(base_amount, "f"),
        "estimated_price": format(price, "f"),
    }


def submit_kraken_order(order: dict[str, Any]) -> dict[str, Any]:
    side = str(order["side"]).lower()
    try:
        if side == "buy":
            return kraken_client.add_market_order(
                pair=order["pair"],
                side="buy",
                volume=order["quote_size"],
                volume_in_quote=True,
                userref=order["client_order_id"],
            )
        return kraken_client.add_market_order(
            pair=order["pair"],
            side="sell",
            volume=order["base_size"],
            volume_in_quote=False,
            userref=order["client_order_id"],
        )
    except KrakenError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kraken order error: {exc}",
        ) from exc


async def execute_auto_trade(
    product_id: str,
    quote_amount: Decimal = Decimal("50"),
    min_confidence: int = 75,
) -> dict[str, Any]:
    normalized = normalize_product_id(product_id)

    if min_confidence < 0 or min_confidence > 100:
        raise HTTPException(
            status_code=400,
            detail="min_confidence must be between 0 and 100",
        )

    if quote_amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="quote_amount must be greater than zero",
        )

    try:
        market_data = kraken_client.get_market_snapshot(normalized)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kraken market data error: {exc}",
        ) from exc

    # ML is opt-in and only overrides the LLM signal when a fresh, schema-valid
    # artifact and completed OHLCV candles are supplied by the market client.
    ml_analysis = None
    if settings.ml_enabled and not settings.ml_kill_switch and market_data.get("ohlcv"):
        try:
            from .ml.features import normalize_completed_ohlcv, make_features
            from .ml.model import load_artifact, predict
            candles = normalize_completed_ohlcv(market_data["ohlcv"])
            feature_rows = make_features(candles)
            if feature_rows:
                ml_analysis = predict(load_artifact(settings.ml_model_path), feature_rows[-1], threshold=settings.ml_confidence_threshold)
        except (OSError, ValueError, KeyError, ImportError):
            ml_analysis = None

    try:
        analysis = await godmod3_client.analyze(normalized, market_data)
    except Godmod3Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    base_response: dict[str, Any] = {
        "product_id": normalized,
        "pair": to_kraken_pair(normalized),
        "action": analysis.action,
        "confidence": analysis.confidence,
        "rationale": analysis.rationale,
        "order_ready": False,
        "submitted": False,
    }

    if ml_analysis is not None and ml_analysis.signal != "HOLD":
        base_response.update({"action": ml_analysis.signal, "confidence": round(ml_analysis.confidence * 100, 2), "strategy": "ML"})
        analysis = analysis.model_copy(update={"action": ml_analysis.signal, "confidence": round(ml_analysis.confidence * 100, 2), "strategy": "ML"})

    if analysis.action == "HOLD":
        return {"status": "hold", **base_response}

    if analysis.confidence < min_confidence:
        return {
            "status": "low_confidence",
            **base_response,
            "minimum_confidence": min_confidence,
        }

    try:
        balances = (kraken_client.get_account().get("balances") or {})
    except KrakenError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kraken balance error: {exc}",
        ) from exc

    usd = kraken_asset_balance(balances, "ZUSD", "USD", "USDT", "ZUSDT")
    btc = kraken_asset_balance(balances, "XXBT", "XBT", "BTC")
    price = decimal_from_value(market_data.get("product", {}).get("price"))
    sized_quote = quote_amount

    if analysis.action == "BUY":
        affordable = floor_to_increment(usd * Decimal("0.96"), Decimal("0.01"))
        sized_quote = min(quote_amount, affordable)
        if sized_quote < MIN_LIVE_QUOTE:
            return {
                "status": "insufficient_funds",
                **base_response,
                "usd_balance": format(usd, "f"),
                "needed": format(MIN_LIVE_QUOTE, "f"),
            }
    else:
        sellable = floor_to_increment(btc * price * Decimal("0.98"), Decimal("0.01"))
        sized_quote = min(quote_amount, sellable)
        if sized_quote < MIN_LIVE_QUOTE:
            return {
                "status": "insufficient_position",
                **base_response,
                "btc_balance": format(btc, "f"),
                "usd_value": format(sellable, "f"),
            }

    signal = WebhookSignal(
        signal_id=f"KRAKEN-{uuid4()}",
        product_id=normalized,
        action=analysis.action,
        quote_amount=sized_quote,
        strategy="AUTO",
    )
    order = build_market_order(signal, market_data.get("product", {}))
    ready_response = {
        **base_response,
        "order_ready": True,
        "minimum_confidence": min_confidence,
        "order": order,
    }

    if settings.paused:
        return {"status": "paused", **ready_response}

    if not settings.live_trading:
        return {
            "status": "dry_run",
            **ready_response,
            "live_trading": False,
        }

    result = submit_kraken_order(order)
    return {
        "status": "submitted",
        **ready_response,
        "submitted": True,
        "live_trading": True,
        "kraken_response": result,
    }


async def run_autonomous_scan() -> dict[str, Any]:
    global _scan_in_progress, _last_trade_at, _last_scan_result

    if _scan_in_progress:
        result = {
            "status": "skipped",
            "reason": "scan_in_progress",
            "product_id": AUTONOMOUS_PRODUCT_ID,
            "submitted": False,
        }
        _last_scan_result = result
        return result

    _scan_in_progress = True

    try:
        now = datetime.now(timezone.utc)

        if _last_trade_at is not None:
            elapsed = (now - _last_trade_at).total_seconds()
            remaining = AUTONOMOUS_TRADE_COOLDOWN_SECONDS - elapsed
            if remaining > 0:
                result = {
                    "status": "skipped",
                    "reason": "trade_cooldown",
                    "product_id": AUTONOMOUS_PRODUCT_ID,
                    "submitted": False,
                    "cooldown_remaining_seconds": int(remaining),
                }
                _last_scan_result = result
                return result

        result = await execute_auto_trade(
            AUTONOMOUS_PRODUCT_ID,
            AUTONOMOUS_QUOTE_AMOUNT,
            AUTONOMOUS_MIN_CONFIDENCE,
        )
        if result.get("submitted"):
            _last_trade_at = now
        _last_scan_result = result
        return result

    except HTTPException as exc:
        result = {
            "status": "error",
            "reason": "http_error",
            "product_id": AUTONOMOUS_PRODUCT_ID,
            "submitted": False,
            "detail": exc.detail,
            "status_code": exc.status_code,
        }
        _last_scan_result = result
        return result
    except Exception as exc:
        result = {
            "status": "error",
            "reason": "scan_failed",
            "product_id": AUTONOMOUS_PRODUCT_ID,
            "submitted": False,
            "detail": str(exc),
        }
        _last_scan_result = result
        return result
    finally:
        _scan_in_progress = False


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kraken Live Bot</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, sans-serif; background:#140b0b; color:#ffe8e8; margin:0; }
    main { max-width: 920px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0 0 8px; }
    .muted { color:#d79a9a; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap:12px; margin: 20px 0; }
    .card { background:#22100d; border:1px solid #4a1d1d; border-radius:12px; padding:16px; }
    .ok { color:#5eead4; } .bad { color:#fda4af; }
    pre { background:#110606; padding:12px; overflow:auto; border-radius:8px; }
  </style>
</head>
<body>
  <main>
    <h1>Kraken Live Bot</h1>
    <p class="muted">XBTUSD autonomous scanner. Portfolio lives in Kraken.</p>
    <div class="grid" id="stats"></div>
    <div class="card">
      <h3>Account / last scan</h3>
      <pre id="scan">loading...</pre>
    </div>
  </main>
  <script>
    async function load() {
      const health = await fetch('/health').then(r => r.json());
      const account = await fetch('/kraken/account').then(r => r.json()).catch(() => ({error: 'unavailable'}));
      document.getElementById('stats').innerHTML = `
        <div class="card"><div class="muted">Paused</div><div class="${health.paused?'bad':'ok'}">${health.paused}</div></div>
        <div class="card"><div class="muted">Live trading</div><div class="${health.live_trading?'ok':'bad'}">${health.live_trading}</div></div>
        <div class="card"><div class="muted">Kraken</div><div>${health.kraken_configured}</div></div>
        <div class="card"><div class="muted">Analyzer</div><div>${(health.analysis_providers||[]).join(', ')||health.godmod3_configured}</div></div>
      `;
      document.getElementById('scan').textContent = JSON.stringify({health: health.autonomous, account}, null, 2);
    }
    load();
    setInterval(load, 15000);
  </script>
</body>
</html>
"""


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "app": settings.app_name,
        "status": "ok",
        "version": CODE_VERSION,
        "broker": "kraken",
        "dashboard": "/dashboard",
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/health")
async def health() -> dict[str, Any]:
    kraken_private: dict[str, Any] = {"ok": False}
    try:
        balances = kraken_client.get_account().get("balances") or {}
        usd = kraken_asset_balance(balances, "ZUSD", "USD", "USDT", "ZUSDT")
        btc = kraken_asset_balance(balances, "XXBT", "XBT", "BTC")
        kraken_private = {
            "ok": True,
            "usd": format(usd, "f"),
            "btc": format(btc, "f"),
        }
    except KrakenError as exc:
        kraken_private = {"ok": False, "error": str(exc)}

    return {
        "status": "ok",
        "code_version": CODE_VERSION,
        "autonomous_enabled": AUTONOMOUS_ENABLED,
        "live_trading": settings.live_trading,
        "paused": settings.paused,
        "kraken_configured": kraken_client.configured,
        "kraken_private": kraken_private,
        "godmod3_configured": godmod3_client.configured,
        "analysis_providers": godmod3_client.available_providers(),
        "last_analysis_provider": godmod3_client.last_provider,
        "autonomous": {
            "product_id": AUTONOMOUS_PRODUCT_ID,
            "pair": to_kraken_pair(AUTONOMOUS_PRODUCT_ID),
            "scan_seconds": AUTONOMOUS_SCAN_SECONDS,
            "quote_amount": str(AUTONOMOUS_QUOTE_AMOUNT),
            "min_confidence": AUTONOMOUS_MIN_CONFIDENCE,
            "trade_cooldown_seconds": AUTONOMOUS_TRADE_COOLDOWN_SECONDS,
            "scan_in_progress": _scan_in_progress,
            "last_trade_at": (
                _last_trade_at.isoformat()
                if _last_trade_at is not None
                else None
            ),
            "last_scan": _last_scan_result,
        },
    }


@app.get("/godmod3/health")
async def godmod3_health() -> dict[str, Any]:
    return {
        "configured": godmod3_client.configured,
        "analysis_only": True,
        "live_trading": False,
        "primary": "claude",
        "providers": godmod3_client.available_providers(),
        "last_provider": godmod3_client.last_provider,
    }


@app.get("/kraken/account")
async def kraken_account() -> dict[str, Any]:
    try:
        return kraken_client.get_account()
    except KrakenError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/analyze/{product_id}", response_model=Godmod3Analysis)
async def analyze_product(product_id: str) -> Godmod3Analysis:
    normalized = normalize_product_id(product_id)
    try:
        market_data = kraken_client.get_market_snapshot(normalized)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kraken market data error: {exc}",
        ) from exc
    try:
        return await godmod3_client.analyze(normalized, market_data)
    except Godmod3Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/auto/{product_id}")
async def auto_trade(
    product_id: str,
    quote_amount: Decimal = Decimal("50"),
    min_confidence: int = 75,
    x_webhook_secret: str | None = Header(
        default=None,
        alias="X-Webhook-Secret",
    ),
) -> dict[str, Any]:
    require_webhook_secret(x_webhook_secret)
    return await execute_auto_trade(product_id, quote_amount, min_confidence)
