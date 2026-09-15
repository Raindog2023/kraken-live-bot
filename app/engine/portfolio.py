from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    product_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quote_amount REAL NOT NULL,
    base_amount REAL,
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    realized_pnl REAL,
    meta TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    position_id TEXT,
    exchange TEXT NOT NULL,
    product_id TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    quote_amount REAL NOT NULL,
    pnl REAL,
    pnl_pct REAL,
    reason TEXT,
    strategy TEXT,
    created_at TEXT NOT NULL,
    meta TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
"""


class Portfolio:
    """SQLite-persisted positions, trades, and PnL accounting."""

    def __init__(self, db_path: str | Path | None = None):
        path = Path(db_path) if db_path else Path(settings.data_dir) / "portfolio.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- positions ---------------------------------------------------------

    def open_position(
        self,
        position_id: str,
        exchange: str,
        product_id: str,
        action: str,
        entry_price: float,
        quote_amount: float,
        base_amount: float | None = None,
        meta: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO positions"
            "(position_id, exchange, product_id, action, entry_price,"
            " quote_amount, base_amount, status, opened_at, meta)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                position_id, exchange, product_id, action, entry_price,
                quote_amount, base_amount, "open",
                datetime.now(timezone.utc).isoformat(), meta,
            ),
        )
        self._conn.commit()

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        realized_pnl: float,
    ) -> None:
        self._conn.execute(
            "UPDATE positions SET status='closed', closed_at=?,"
            " exit_price=?, realized_pnl=? WHERE position_id=?",
            (
                datetime.now(timezone.utc).isoformat(),
                exit_price, realized_pnl, position_id,
            ),
        )
        self._conn.commit()

    def get_open_positions(self, exchange: str | None = None) -> list[dict[str, Any]]:
        if exchange:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE status='open' AND exchange=?",
                (exchange,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE status='open'"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_position(self, position_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM positions WHERE position_id=?",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None

    def open_exposure(self, exchange: str | None = None) -> Decimal:
        positions = self.get_open_positions(exchange)
        return sum(Decimal(str(p["quote_amount"])) for p in positions)

    # -- trades ------------------------------------------------------------

    def record_trade(
        self,
        trade_id: str,
        exchange: str,
        product_id: str,
        action: str,
        price: float,
        quote_amount: float,
        pnl: float | None = None,
        pnl_pct: float | None = None,
        reason: str | None = None,
        strategy: str | None = None,
        position_id: str | None = None,
        meta: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trades"
            "(trade_id, position_id, exchange, product_id, action, price,"
            " quote_amount, pnl, pnl_pct, reason, strategy, created_at, meta)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade_id, position_id, exchange, product_id, action, price,
                quote_amount, pnl, pnl_pct, reason, strategy,
                datetime.now(timezone.utc).isoformat(), meta,
            ),
        )
        self._conn.commit()

    def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def daily_pnl(self) -> Decimal:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE created_at >= ? AND pnl IS NOT NULL",
            (today,),
        ).fetchone()
        return Decimal(str(row[0]))

    def performance_metrics(self) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT pnl, pnl_pct FROM trades WHERE pnl IS NOT NULL ORDER BY created_at"
        ).fetchall()
        if not rows:
            return {
                "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                "total_pnl": "0", "win_rate": 0.0, "average_win": "0",
                "average_loss": "0", "max_drawdown": "0", "current_streak": 0,
                "best_trade": "0", "worst_trade": "0",
            }

        pnls = [Decimal(str(r["pnl"])) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]

        running = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        streak = 0
        for p in reversed(pnls):
            if p > 0:
                streak += 1
            elif p < 0:
                streak -= 1
            else:
                break

        return {
            "total_trades": len(pnls),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "total_pnl": str(sum(pnls)),
            "win_rate": len(wins) / len(pnls) * 100 if pnls else 0.0,
            "average_win": str(sum(wins) / len(wins)) if wins else "0",
            "average_loss": str(sum(losses) / len(losses)) if losses else "0",
            "max_drawdown": str(max_dd),
            "current_streak": streak,
            "best_trade": str(max(wins)) if wins else "0",
            "worst_trade": str(-min(losses)) if losses else "0",
        }

    def reset(self) -> None:
        self._conn.execute("DELETE FROM trades")
        self._conn.execute("DELETE FROM positions")
        self._conn.commit()


portfolio = Portfolio()
