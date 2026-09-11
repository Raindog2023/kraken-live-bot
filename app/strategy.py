"""Cost-aware trading decisions and position risk state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

Action = Literal["BUY", "SELL", "HOLD"]

Decision = tuple[Action, str]


@dataclass(frozen=True)
class CostModel:
    fee_bps: float
    slippage_bps: float
    min_edge_multiple: float

    @property
    def round_trip_cost_pct(self) -> float:
        return 2 * (self.fee_bps + self.slippage_bps) / 100

    @property
    def required_edge_pct(self) -> float:
        return self.round_trip_cost_pct * self.min_edge_multiple


def momentum_score(summary: dict[str, Any], change_24h: float = 0.0) -> float:
    """Weighted short-horizon momentum, expressed in percent."""
    return (
        ((summary.get("change_5m_percent") or 0.0) * 2.0)
        + ((summary.get("change_15m_percent") or 0.0) * 1.5)
        + (summary.get("change_1h_percent") or 0.0)
        + ((summary.get("change_full_window_percent") or 0.0) * 0.25)
        + (change_24h * 0.35)
    )


def cost_aware_action(score_pct: float, costs: CostModel) -> Decision:
    """Only trade when the momentum edge can pay for a round trip."""
    required = costs.required_edge_pct
    if score_pct >= required:
        return "BUY", f"momentum {score_pct:.3f}% >= required edge {required:.3f}%"
    if score_pct <= -required:
        return "SELL", f"momentum {score_pct:.3f}% <= -required edge {required:.3f}%"
    return (
        "HOLD",
        f"momentum {score_pct:.3f}% below round-trip cost hurdle {required:.3f}%",
    )


@dataclass
class Position:
    entry_price: Decimal
    quote_size: Decimal
    opened_at: datetime

    def unrealized_pct(self, price: Decimal) -> float:
        if self.entry_price <= 0:
            return 0.0
        return float((price - self.entry_price) / self.entry_price * 100)


@dataclass
class RiskState:
    """In-process risk accounting for the autonomous loop."""

    daily_loss_limit: Decimal
    stop_loss_pct: float
    take_profit_pct: float
    position: Position | None = None
    realized_pnl: Decimal = Decimal("0")
    day: datetime | None = None

    def _roll_day(self, now: datetime) -> None:
        today = now.astimezone(timezone.utc).date()
        if self.day is None or self.day.date() != today:
            self.day = datetime.combine(
                today, datetime.min.time(), tzinfo=timezone.utc
            )
            self.realized_pnl = Decimal("0")

    def halted(self, now: datetime) -> bool:
        self._roll_day(now)
        return self.realized_pnl <= -abs(self.daily_loss_limit)

    def open_position(
        self, price: Decimal, quote_size: Decimal, now: datetime
    ) -> None:
        self._roll_day(now)
        self.position = Position(
            entry_price=price, quote_size=quote_size, opened_at=now
        )

    def close_position(self, price: Decimal, now: datetime) -> Decimal:
        self._roll_day(now)
        if self.position is None:
            return Decimal("0")
        pnl = self.position.quote_size * (
            (price - self.position.entry_price) / self.position.entry_price
        )
        self.realized_pnl += pnl
        self.position = None
        return pnl

    def exit_reason(self, price: Decimal) -> str | None:
        if self.position is None:
            return None
        change = self.position.unrealized_pct(price)
        if change <= -abs(self.stop_loss_pct):
            return "stop_loss"
        if change >= abs(self.take_profit_pct):
            return "take_profit"
        return None

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        self._roll_day(now)
        return {
            "position": (
                None
                if self.position is None
                else {
                    "entry_price": format(self.position.entry_price, "f"),
                    "quote_size": format(self.position.quote_size, "f"),
                    "opened_at": self.position.opened_at.isoformat(),
                    "age_seconds": int(
                        (now - self.position.opened_at).total_seconds()
                    ),
                }
            ),
            "realized_pnl_today": format(self.realized_pnl, "f"),
            "daily_loss_limit": format(self.daily_loss_limit, "f"),
            "halted_for_the_day": self.realized_pnl
            <= -abs(self.daily_loss_limit),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
        }


def next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(
        (now + timedelta(days=1)).astimezone(timezone.utc).date(),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
