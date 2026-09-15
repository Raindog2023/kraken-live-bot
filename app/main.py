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


# Position tracking for stop-loss/take-profit
_open_positions: dict[str, dict[str, Any]] = {}

# Performance tracking
_trade_history: list[dict[str, Any]] = []
_performance_metrics: dict[str, Any] = {
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "total_pnl": Decimal("0"),
    "win_rate": 0.0,
    "average_win": Decimal("0"),
    "average_loss": Decimal("0"),
    "max_drawdown": Decimal("0"),
    "current_streak": 0,
    "best_trade": Decimal("0"),
    "worst_trade": Decimal("0"),
}


def check_stop_loss_take_profit(product_id: str, current_price: float) -> dict[str, Any]:
    """Check if any open positions should be closed due to stop-loss or take-profit."""
    positions_to_close = []

    for position_id, position in _open_positions.items():
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
            # For long positions
            pnl_pct = (current_price - entry_price) / entry_price

            # Take profit check
            if pnl_pct >= take_profit_pct:
                positions_to_close.append({
                    "position_id": position_id,
                    "reason": "take_profit",
                    "pnl_pct": pnl_pct,
                    "action": "SELL"
                })
            # Stop loss check
            elif pnl_pct <= -stop_loss_pct:
                positions_to_close.append({
                    "position_id": position_id,
                    "reason": "stop_loss",
                    "pnl_pct": pnl_pct,
                    "action": "SELL"
                })
            # Trailing stop check
            elif pnl_pct > 0:
                trailing_stop_price = entry_price * (1 + (pnl_pct - trailing_stop_pct))
                if current_price < trailing_stop_price:
                    positions_to_close.append({
                        "position_id": position_id,
                        "reason": "trailing_stop",
                        "pnl_pct": pnl_pct,
                        "action": "SELL"
                    })

        elif action == "SELL":
            # For short positions
            pnl_pct = (entry_price - current_price) / entry_price

            # Take profit check
            if pnl_pct >= take_profit_pct:
                positions_to_close.append({
                    "position_id": position_id,
                    "reason": "take_profit",
                    "pnl_pct": pnl_pct,
                    "action": "BUY"
                })
            # Stop loss check
            elif pnl_pct <= -stop_loss_pct:
                positions_to_close.append({
                    "position_id": position_id,
                    "reason": "stop_loss",
                    "pnl_pct": pnl_pct,
                    "action": "BUY"
                })

    return {"positions_to_close": positions_to_close}


def track_position(signal_id: str, product_id: str, action: str, entry_price: float, quote_amount: float) -> None:
    """Track a new position for stop-loss/take-profit monitoring."""
    _open_positions[signal_id] = {
        "position_id": signal_id,
        "product_id": product_id,
        "action": action,
        "entry_price": entry_price,
        "quote_amount": quote_amount,
        "entry_time": datetime.now(timezone.utc).isoformat(),
    }


def record_trade(trade_data: dict[str, Any]) -> None:
    """Record a trade in the performance history."""
    _trade_history.append(trade_data)
    update_performance_metrics()


def update_performance_metrics() -> None:
    """Update performance metrics based on trade history."""
    if not _trade_history:
        return

    total_trades = len(_trade_history)
    winning_trades = sum(1 for trade in _trade_history if trade.get("pnl", 0) > 0)
    losing_trades = sum(1 for trade in _trade_history if trade.get("pnl", 0) < 0)

    total_pnl = sum(Decimal(str(trade.get("pnl", 0))) for trade in _trade_history)

    wins = [Decimal(str(trade.get("pnl", 0))) for trade in _trade_history if trade.get("pnl", 0) > 0]
    losses = [abs(Decimal(str(trade.get("pnl", 0)))) for trade in _trade_history if trade.get("pnl", 0) < 0]

    average_win = sum(wins) / len(wins) if wins else Decimal("0")
    average_loss = sum(losses) / len(losses) if losses else Decimal("0")

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    # Calculate max drawdown
    running_pnl = Decimal("0")
    max_pnl = Decimal("0")
    max_drawdown = Decimal("0")

    for trade in _trade_history:
        running_pnl += Decimal(str(trade.get("pnl", 0)))
        if running_pnl > max_pnl:
            max_pnl = running_pnl
        drawdown = max_pnl - running_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # Calculate current streak
    current_streak = 0
    for trade in reversed(_trade_history):
        if trade.get("pnl", 0) > 0:
            current_streak += 1
        elif trade.get("pnl", 0) < 0:
            current_streak -= 1
        else:
            break

    best_trade = max(wins) if wins else Decimal("0")
    worst_trade = min(losses) if losses else Decimal("0")

    _performance_metrics.update({
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "average_win": average_win,
        "average_loss": average_loss,
        "max_drawdown": max_drawdown,
        "current_streak": current_streak,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    })


CODE_VERSION = "2.9.0-profit-optimized"
AUTONOMOUS_ENABLED = True
AUTONOMOUS_PRODUCT_ID = "BTC-USD"
AUTONOMOUS_QUOTE_AMOUNT = Decimal("50")  # Increased base position size
AUTONOMOUS_MIN_CONFIDENCE = 75  # Lowered for more opportunities
# Scores from 80-89 are observation-only; they cannot open a new position.
MONITOR_CONFIDENCE = 70
AUTONOMOUS_SCAN_SECONDS = 30  # Faster scanning for better opportunities
AUTONOMOUS_TRADE_COOLDOWN_SECONDS = 60  # Quicker re-entry
MIN_LIVE_QUOTE = Decimal("5")
MAX_POSITION_SIZE = Decimal("200")  # Maximum position size
VOLATILITY_MULTIPLIER = 1.5  # Position size multiplier based on volatility

_scan_in_progress = False
_last_trade_at: datetime | None = None
_last_scan_result: dict[str, Any] | None = {"status": "initializing", "reason": "awaiting_first_scan", "product_id": "BTC-USD", "submitted": False}


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
    version="2.9.0-profit-optimized",
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


def calculate_dynamic_position_size(
    base_amount: Decimal,
    confidence: int,
    volatility: float = 1.0,
) -> Decimal:
    """Calculate dynamic position size based on confidence and volatility."""
    if confidence < 50:
        return base_amount * Decimal("0.5")  # Reduce size for low confidence

    confidence_factor = Decimal(str(confidence)) / Decimal("100")
    volatility_factor = Decimal(str(min(volatility, VOLATILITY_MULTIPLIER)))

    dynamic_size = base_amount * confidence_factor * volatility_factor

    # Cap at maximum position size
    return min(dynamic_size, MAX_POSITION_SIZE)


def calculate_volatility(market_data: dict[str, Any]) -> float:
    """Calculate volatility from market data."""
    candles = market_data.get("candles", [])
    if not candles or len(candles) < 10:
        return 1.0  # Default volatility

    try:
        closes = [float(candle.get("close", 0)) for candle in candles[-10:]]
        if not closes or any(c <= 0 for c in closes):
            return 1.0

        returns = [abs((closes[i] - closes[i-1]) / closes[i-1]) for i in range(1, len(closes))]
        avg_volatility = sum(returns) / len(returns) if returns else 0.01

        # Normalize to 0.5-2.0 range
        normalized = max(0.5, min(2.0, avg_volatility * 50))
        return normalized
    except (ZeroDivisionError, ValueError, TypeError):
        return 1.0


def calculate_multi_timeframe_score(multi_timeframe_data: dict[str, Any]) -> float:
    """Calculate combined score from multiple timeframes."""
    scores = []

    for timeframe, candles in multi_timeframe_data.items():
        if not candles or len(candles) < 3:
            continue

        try:
            closes = [float(candle.get("close", 0)) for candle in candles[-3:]]
            if len(closes) < 2 or any(c <= 0 for c in closes):
                continue

            change = (closes[-1] - closes[0]) / closes[0]

            # Weight different timeframes differently
            if timeframe == "5m":
                weight = 2.0  # Short-term most important
            elif timeframe == "15m":
                weight = 1.5  # Medium-term
            else:  # 1h
                weight = 1.0  # Long-term confirmation

            scores.append(change * weight)
        except (ZeroDivisionError, ValueError, TypeError):
            continue

    if not scores:
        return 0.0

    return sum(scores) / len(scores)


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
        multi_timeframe_data = kraken_client.get_multi_timeframe_data(normalized)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kraken market data error: {exc}",
        ) from exc

    # Calculate volatility for dynamic position sizing
    volatility = calculate_volatility(market_data)

    # Multi-timeframe signal confirmation
    multi_timeframe_score = calculate_multi_timeframe_score(multi_timeframe_data)

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
        "multi_timeframe_score": multi_timeframe_score,
    }

    # Adjust confidence based on multi-timeframe confirmation
    final_confidence = analysis.confidence
    final_action = analysis.action
    final_strategy = getattr(analysis, "strategy", "LLM") or "LLM"

    if ml_analysis is not None and ml_analysis.signal != "HOLD":
        # Use ML signal with multi-timeframe adjustment
        final_action = ml_analysis.signal
        final_confidence = round(ml_analysis.confidence * 100, 2)
        final_strategy = "ML"

        if multi_timeframe_score > 0.01 and final_action == "BUY":
            final_confidence = min(95, final_confidence + 10)
        elif multi_timeframe_score < -0.01 and final_action == "SELL":
            final_confidence = min(95, final_confidence + 10)
        elif (multi_timeframe_score > 0.01 and final_action == "SELL") or \
             (multi_timeframe_score < -0.01 and final_action == "BUY"):
            final_confidence = max(50, final_confidence - 15)
    else:
        # Use LLM signal with multi-timeframe adjustment
        if multi_timeframe_score > 0.01 and final_action == "BUY":
            final_confidence = min(95, final_confidence + 5)
        elif multi_timeframe_score < -0.01 and final_action == "SELL":
            final_confidence = min(95, final_confidence + 5)
        elif (multi_timeframe_score > 0.01 and final_action == "SELL") or \
             (multi_timeframe_score < -0.01 and final_action == "BUY"):
            final_confidence = max(50, final_confidence - 10)

    base_response.update({
        "action": final_action,
        "confidence": final_confidence,
        "strategy": final_strategy
    })
    analysis = analysis.model_copy(update={"action": final_action, "confidence": final_confidence})

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

    # Calculate dynamic position size based on confidence and volatility
    sized_quote = calculate_dynamic_position_size(quote_amount, analysis.confidence, volatility)

    if analysis.action == "BUY":
        affordable = floor_to_increment(usd * Decimal("0.96"), Decimal("0.01"))
        sized_quote = min(sized_quote, affordable)
        if sized_quote < MIN_LIVE_QUOTE:
            return {
                "status": "insufficient_funds",
                **base_response,
                "usd_balance": format(usd, "f"),
                "needed": format(MIN_LIVE_QUOTE, "f"),
            }
    else:
        sellable = floor_to_increment(btc * price * Decimal("0.98"), Decimal("0.01"))
        sized_quote = min(sized_quote, sellable)
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

    # Track position for stop-loss/take-profit
    if result.get("txid") and settings.live_trading:
        entry_price = decimal_from_value(market_data.get("product", {}).get("price"))
        track_position(
            signal_id=signal.signal_id,
            product_id=normalized,
            action=analysis.action,
            entry_price=float(entry_price),
            quote_amount=sized_quote
        )

    return {
        "status": "submitted",
        **ready_response,
        "submitted": True,
        "live_trading": True,
        "kraken_response": result,
        "volatility": volatility,
        "dynamic_position_size": str(sized_quote),
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
        <div class="card"><div class="muted">Win Rate</div><div class="${health.performance?.win_rate>50?'ok':'bad'}">${health.performance?.win_rate||0}%</div></div>
        <div class="card"><div class="muted">Total PnL</div><div class="${health.performance?.total_pnl>0?'ok':'bad'}">$${health.performance?.total_pnl||0}</div></div>
        <div class="card"><div class="muted">Trades</div><div>${health.performance?.total_trades||0}</div></div>
      `;
      document.getElementById('scan').textContent = JSON.stringify({health: health.autonomous, account, performance: health.performance, risk: health.risk_management}, null, 2);
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
    return {
        "status": "ok",
        "code_version": CODE_VERSION,
        "autonomous_enabled": AUTONOMOUS_ENABLED,
        "live_trading": settings.live_trading,
        "paused": settings.paused,
        "kraken_configured": kraken_client.configured,
        "godmod3_configured": godmod3_client.configured,
        "analysis_providers": godmod3_client.available_providers(),
        "last_analysis_provider": godmod3_client.last_provider,
        "risk_management": {
            "stop_loss_percentage": settings.stop_loss_percentage,
            "take_profit_percentage": settings.take_profit_percentage,
            "trailing_stop_percentage": settings.trailing_stop_percentage,
            "max_position_size": str(MAX_POSITION_SIZE),
        },
        "performance": {
            "total_trades": _performance_metrics["total_trades"],
            "win_rate": _performance_metrics["win_rate"],
            "total_pnl": str(_performance_metrics["total_pnl"]),
        },
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


@app.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Get current open positions with stop-loss/take-profit status."""
    return {
        "open_positions": _open_positions,
        "count": len(_open_positions),
    }


@app.post("/positions/check/{product_id}")
async def check_positions(product_id: str) -> dict[str, Any]:
    """Check and close positions based on stop-loss/take-profit."""
    normalized = normalize_product_id(product_id)

    try:
        ticker = kraken_client.get_ticker(normalized)
        current_price = float(ticker.get("price", 0))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to get current price: {exc}") from exc

    if current_price <= 0:
        raise HTTPException(status_code=400, detail="Invalid current price")

    check_result = check_stop_loss_take_profit(normalized, current_price)

    # Close positions if needed
    closed_positions = []
    for position_info in check_result["positions_to_close"]:
        position_id = position_info["position_id"]
        if position_id in _open_positions:
            position = _open_positions[position_id]

            # Create closing order
            try:
                closing_signal = WebhookSignal(
                    signal_id=f"CLOSE-{position_id}",
                    product_id=normalized,
                    action=position_info["action"],
                    quote_amount=Decimal(str(position.get("quote_amount", "0"))),
                    strategy="STOP_LOSS_TAKE_PROFIT"
                )

                order = build_market_order(closing_signal, {"price": str(current_price)})
                if settings.live_trading and not settings.paused:
                    result = submit_kraken_order(order)
                    closed_positions.append({
                        "position_id": position_id,
                        "reason": position_info["reason"],
                        "pnl_pct": position_info["pnl_pct"],
                        "closed": True,
                        "kraken_response": result
                    })
                else:
                    closed_positions.append({
                        "position_id": position_id,
                        "reason": position_info["reason"],
                        "pnl_pct": position_info["pnl_pct"],
                        "closed": False,
                        "note": "dry_run"
                    })

                # Remove from tracking
                del _open_positions[position_id]

                # Record trade for performance tracking
                if closed_positions[-1].get("closed"):
                    record_trade({
                        "trade_id": position_id,
                        "product_id": normalized,
                        "action": position.get("action"),
                        "entry_price": position.get("entry_price"),
                        "exit_price": current_price,
                        "quote_amount": position.get("quote_amount"),
                        "pnl": position_info["pnl_pct"] * float(position.get("quote_amount", 0)),
                        "pnl_pct": position_info["pnl_pct"],
                        "reason": position_info["reason"],
                        "exit_time": datetime.now(timezone.utc).isoformat(),
                    })

            except Exception as exc:
                closed_positions.append({
                    "position_id": position_id,
                    "reason": position_info["reason"],
                    "pnl_pct": position_info["pnl_pct"],
                    "closed": False,
                    "error": str(exc)
                })

    return {
        "current_price": current_price,
        "positions_checked": len(_open_positions) + len(closed_positions),
        "positions_to_close": check_result["positions_to_close"],
        "closed_positions": closed_positions,
        "remaining_positions": _open_positions,
    }


@app.get("/performance")
async def get_performance() -> dict[str, Any]:
    """Get trading performance metrics."""
    return {
        "metrics": _performance_metrics,
        "recent_trades": _trade_history[-20:],  # Last 20 trades
        "total_trades_recorded": len(_trade_history),
    }


@app.get("/performance/reset")
async def reset_performance() -> dict[str, Any]:
    """Reset performance tracking (use with caution)."""
    global _trade_history, _performance_metrics
    _trade_history = []
    _performance_metrics = {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": Decimal("0"),
        "win_rate": 0.0,
        "average_win": Decimal("0"),
        "average_loss": Decimal("0"),
        "max_drawdown": Decimal("0"),
        "current_streak": 0,
        "best_trade": Decimal("0"),
        "worst_trade": Decimal("0"),
    }
    return {"status": "reset", "message": "Performance metrics reset"}
