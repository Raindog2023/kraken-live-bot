from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from ..config import settings
from ..engine.executor import build_market_order, submit_market_order
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
    scan_pairs,
)
from ..engine.signals import execute_external_signal
from ..exchanges import ExchangeError, kraken_client, normalize_product_id
from ..exchanges.kraken import to_kraken_pair
from ..godmod3_client import Godmod3Analysis, Godmod3Error, godmod3_client
from .schemas import ExternalSignal, WebhookSignal, require_webhook_secret

CODE_VERSION = "3.0.0-platform"

AUTONOMOUS_ENABLED = True
MONITOR_CONFIDENCE = 70

DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Kraken Trading Platform</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, sans-serif; background:#140b0b; color:#ffe8e8; margin:0; }
    main { max-width: 1080px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0 0 8px; }
    h3 { margin: 0 0 10px; font-size: 14px; }
    .muted { color:#d79a9a; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap:12px; margin: 20px 0; }
    .card { background:#22100d; border:1px solid #4a1d1d; border-radius:12px; padding:16px; }
    .ok { color:#5eead4; } .bad { color:#fda4af; } .warn { color:#fbbf24; }
    pre { background:#110606; padding:12px; overflow:auto; border-radius:8px; font-size:11px; }
    table { width:100%; border-collapse:collapse; font-size:12px; }
    td, th { padding:6px 8px; text-align:left; border-bottom:1px solid #3a1717; }
    th { color:#d79a9a; font-weight:600; }
    .BUY { color:#5eead4; } .SELL { color:#fda4af; } .HOLD { color:#8a7a7a; }
    .cols { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
    @media (max-width: 800px) { .cols { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <h1>Kraken Trading Platform</h1>
    <p class="muted">Autonomous scanner &middot; pluggable strategies &middot; <span id="exch"></span></p>
    <div class="grid" id="stats"></div>
    <div class="cols">
      <div class="card"><h3>Open positions</h3><div id="positions">none</div></div>
      <div class="card"><h3>Strategy signals (last scan)</h3><div id="signals">none</div></div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>Recent trades / last scan</h3>
      <pre id="scan">loading...</pre>
    </div>
  </main>
  <script>
    async function load() {
      const health = await fetch('/health').then(r => r.json());
      const perf = await fetch('/performance').then(r => r.json()).catch(() => ({}));
      const pos = await fetch('/positions').then(r => r.json()).catch(() => ({open_positions:[]}));
      document.getElementById('exch').textContent = health.exchange || 'kraken';
      const m = perf.metrics || {};
      document.getElementById('stats').innerHTML = `
        <div class="card"><div class="muted">Status</div><div class="${health.paused?'bad':'ok'}">${health.paused?'PAUSED':'RUNNING'}</div></div>
        <div class="card"><div class="muted">Mode</div><div class="${health.live_trading?'warn':'ok'}">${health.live_trading?'LIVE':'PAPER/DRY'}</div></div>
        <div class="card"><div class="muted">Exchange</div><div>${health.exchange||'kraken'}</div></div>
        <div class="card"><div class="muted">Strategies</div><div>${(health.strategies||[]).join(', ')}</div></div>
        <div class="card"><div class="muted">Win rate</div><div class="${m.win_rate>50?'ok':'bad'}">${(m.win_rate||0).toFixed(1)}%</div></div>
        <div class="card"><div class="muted">Total PnL</div><div class="${parseFloat(m.total_pnl||0)>0?'ok':'bad'}">$${m.total_pnl||0}</div></div>
        <div class="card"><div class="muted">Daily PnL</div><div class="${parseFloat(perf.daily_pnl||0)>=0?'ok':'bad'}">$${perf.daily_pnl||0}</div></div>
        <div class="card"><div class="muted">Max DD</div><div class="bad">$${m.max_drawdown||0}</div></div>
        <div class="card"><div class="muted">Trades</div><div>${m.total_trades||0} (${m.current_streak||0} streak)</div></div>
        <div class="card"><div class="muted">Open</div><div>${pos.count||0}</div></div>
      `;
      const plist = pos.open_positions || [];
      document.getElementById('positions').innerHTML = plist.length
        ? '<table><tr><th>id</th><th>pair</th><th>side</th><th>entry</th><th>size</th></tr>' +
          plist.map(p => `<tr><td>${(p.position_id||'').slice(0,14)}</td><td>${p.product_id}</td>` +
            `<td class="${p.action}">${p.action}</td><td>${p.entry_price}</td><td>$${p.quote_amount}</td></tr>`).join('') +
          '</table>' : '<span class="muted">no open positions</span>';
      const last = (health.autonomous||{}).last_scan || {};
      const sigs = last.signals || [];
      document.getElementById('signals').innerHTML = sigs.length
        ? '<table><tr><th>strategy</th><th>action</th><th>conf</th></tr>' +
          sigs.map(s => `<tr><td>${s.strategy}</td><td class="${s.action}">${s.action}</td><td>${s.confidence}</td></tr>`).join('') +
          '</table>' : `<span class="muted">${last.status||'no scan yet'}</span>`;
      document.getElementById('scan').textContent = JSON.stringify(
        {last_scan: last, recent_trades: (perf.recent_trades||[]).slice(0,8)}, null, 2);
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
            "paper_trading": getattr(settings, "paper_trading", True),
            "paused": settings.paused,
            "exchange": exchange.name,
            "exchange_configured": exchange.configured,
            "strategies": [s.name for s in aggregator.strategies],
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
                "pairs": scan_pairs(),
                "pair": exchange.to_product_id(AUTONOMOUS_PRODUCT_ID),
                "scan_seconds": AUTONOMOUS_SCAN_SECONDS,
                "quote_amount": str(AUTONOMOUS_QUOTE_AMOUNT),
                "min_confidence": AUTONOMOUS_MIN_CONFIDENCE,
                "trade_cooldown_seconds": AUTONOMOUS_TRADE_COOLDOWN_SECONDS,
                **scan,
            },
            "inbound_signals": {
                "endpoint": "/webhook",
                "enabled": settings.webhook_signals_enabled,
                "default_quote": settings.webhook_default_quote,
                "min_confidence": settings.webhook_min_confidence,
                "secret_configured": bool(
                    settings.webhook_secret
                    and settings.webhook_secret != "CHANGE_ME"),
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

    @app.post("/webhook")
    async def inbound_signal(
        signal: ExternalSignal,
        x_webhook_secret: str | None = Header(
            default=None,
            alias="X-Webhook-Secret",
        ),
    ) -> dict[str, Any]:
        """Execute a signal posted by an external provider (e.g. signal8)."""
        require_webhook_secret(x_webhook_secret)
        try:
            return execute_external_signal(exchange, signal, store)
        except ExchangeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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
