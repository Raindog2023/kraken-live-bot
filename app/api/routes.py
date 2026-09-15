from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from ..config import settings
from ..engine.executor import build_market_order, submit_market_order
from ..engine.portfolio import portfolio
from ..engine.risk import MAX_POSITION_SIZE, decimal_from_value
from ..engine.scanner import (
    AUTONOMOUS_MIN_CONFIDENCE,
    AUTONOMOUS_PRODUCT_ID,
    AUTONOMOUS_QUOTE_AMOUNT,
    AUTONOMOUS_SCAN_SECONDS,
    AUTONOMOUS_TRADE_COOLDOWN_SECONDS,
    check_stop_loss_take_profit,
    execute_auto_trade,
    get_scan_state,
)
from ..exchanges import ExchangeError, kraken_client, normalize_product_id
from ..godmod3_client import Godmod3Analysis, Godmod3Error, godmod3_client
from ..exchanges.kraken import to_kraken_pair
from .schemas import WebhookSignal, require_webhook_secret

CODE_VERSION = "3.0.0-platform"

AUTONOMOUS_ENABLED = True
MONITOR_CONFIDENCE = 70

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


def register_routes(app: FastAPI, *, exchange, aggregator, store) -> None:
    """Register every endpoint against the wired exchange/strategy/store."""

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "app": settings.app_name,
            "status": "ok",
            "version": CODE_VERSION,
            "broker": exchange.name,
            "dashboard": "/dashboard",
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        return DASHBOARD_HTML

    @app.get("/health")
    async def health() -> dict[str, Any]:
        perf = store.performance_metrics()
        scan = get_scan_state()
        return {
            "status": "ok",
            "code_version": CODE_VERSION,
            "autonomous_enabled": True,
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
                "daily_loss_limit": str(settings.ml_daily_loss_limit),
            },
            "performance": {
                "total_trades": perf["total_trades"],
                "win_rate": perf["win_rate"],
                "total_pnl": perf["total_pnl"],
            },
            "autonomous": {
                "product_id": AUTONOMOUS_PRODUCT_ID,
                "pair": exchange.to_product_id(AUTONOMOUS_PRODUCT_ID),
                "scan_seconds": AUTONOMOUS_SCAN_SECONDS,
                "quote_amount": str(AUTONOMOUS_QUOTE_AMOUNT),
                "min_confidence": AUTONOMOUS_MIN_CONFIDENCE,
                "trade_cooldown_seconds": AUTONOMOUS_TRADE_COOLDOWN_SECONDS,
                **scan,
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
            return exchange.get_account()
        except ExchangeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/analyze/{product_id}", response_model=Godmod3Analysis)
    async def analyze_product(product_id: str) -> Godmod3Analysis:
        normalized = normalize_product_id(product_id)
        try:
            market_data = exchange.get_market_snapshot(normalized)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{exchange.name} market data error: {exc}",
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
        return await execute_auto_trade(
            exchange, product_id, quote_amount, min_confidence,
            aggregator, store)

    @app.get("/positions")
    async def get_positions() -> dict[str, Any]:
        """Get current open positions with stop-loss/take-profit status."""
        positions = store.get_open_positions()
        return {"open_positions": positions, "count": len(positions)}

    @app.post("/positions/check/{product_id}")
    async def check_positions(product_id: str) -> dict[str, Any]:
        """Check and close positions based on stop-loss/take-profit."""
        normalized = normalize_product_id(product_id)

        try:
            ticker = exchange.get_ticker(normalized)
            current_price = float(decimal_from_value(ticker.get("price")))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to get current price: {exc}",
            ) from exc

        if current_price <= 0:
            raise HTTPException(status_code=400, detail="Invalid current price")

        to_close = check_stop_loss_take_profit(
            normalized, current_price, store)
        closed_positions = []

        for info in to_close:
            position_id = info["position_id"]
            position = store.get_position(position_id)
            if position is None:
                continue

            try:
                closing_signal = WebhookSignal(
                    signal_id=f"CLOSE-{position_id}",
                    product_id=normalized,
                    action=info["action"],
                    quote_amount=Decimal(str(position.get("quote_amount", "0"))),
                    strategy="STOP_LOSS_TAKE_PROFIT",
                )
                order = build_market_order(
                    exchange, closing_signal, {"price": str(current_price)})
                closed = False
                result: dict[str, Any] | None = None
                if settings.live_trading and not settings.paused:
                    result = submit_market_order(exchange, order)
                    closed = True

                realized_pnl = (
                    info["pnl_pct"] * float(position.get("quote_amount", 0)))
                store.close_position(position_id, current_price, realized_pnl)
                if closed:
                    store.record_trade(
                        trade_id=position_id,
                        position_id=position_id,
                        exchange=exchange.name,
                        product_id=normalized,
                        action=position.get("action"),
                        price=current_price,
                        quote_amount=float(position.get("quote_amount", 0)),
                        pnl=realized_pnl,
                        pnl_pct=info["pnl_pct"],
                        reason=info["reason"],
                        strategy="STOP_LOSS_TAKE_PROFIT",
                    )
                closed_positions.append({
                    "position_id": position_id,
                    "reason": info["reason"],
                    "pnl_pct": info["pnl_pct"],
                    "closed": closed,
                    **({"exchange_response": result} if result else
                       {"note": "dry_run"}),
                })
            except Exception as exc:
                closed_positions.append({
                    "position_id": position_id,
                    "reason": info["reason"],
                    "pnl_pct": info["pnl_pct"],
                    "closed": False,
                    "error": str(exc),
                })

        return {
            "current_price": current_price,
            "positions_to_close": to_close,
            "closed_positions": closed_positions,
            "remaining_positions": store.get_open_positions(),
        }

    @app.get("/performance")
    async def get_performance() -> dict[str, Any]:
        """Get trading performance metrics."""
        return {
            "metrics": store.performance_metrics(),
            "recent_trades": store.get_recent_trades(20),
            "daily_pnl": str(store.daily_pnl()),
        }

    @app.post("/performance/reset")
    async def reset_performance(
        x_webhook_secret: str | None = Header(
            default=None, alias="X-Webhook-Secret"),
    ) -> dict[str, Any]:
        """Reset performance tracking (webhook-secret guarded)."""
        require_webhook_secret(x_webhook_secret)
        store.reset()
        return {"status": "reset", "message": "Performance metrics reset"}
