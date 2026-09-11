from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.godmod3_client import local_momentum_analysis
from app.strategy import CostModel, RiskState, cost_aware_action, momentum_score

COSTS = CostModel(fee_bps=26.0, slippage_bps=2.0, min_edge_multiple=2.0)


def candles(closes):
    return [
        {
            "start": index * 300,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1,
        }
        for index, close in enumerate(closes)
    ]


def test_required_edge_covers_round_trip():
    assert COSTS.round_trip_cost_pct == 0.56
    assert COSTS.required_edge_pct == 1.12


def test_noise_does_not_trade():
    action, reason = cost_aware_action(0.02, COSTS)
    assert action == "HOLD"
    assert "hurdle" in reason


def test_edge_above_hurdle_trades_both_ways():
    assert cost_aware_action(1.5, COSTS)[0] == "BUY"
    assert cost_aware_action(-1.5, COSTS)[0] == "SELL"


def test_momentum_score_weights_recent_changes():
    summary = {"change_5m_percent": 1.0, "change_1h_percent": 1.0}
    assert momentum_score(summary) == 3.0


def test_local_momentum_holds_on_drifting_noise():
    drift = [100 + index * 0.001 for index in range(60)]
    assert local_momentum_analysis("BTC-USD", {"candles": candles(drift)}).action == "HOLD"


def test_local_momentum_buys_a_real_move():
    rally = [100.0] * 55 + [101.0, 102.0, 103.5, 105.0, 107.0]
    assert local_momentum_analysis("BTC-USD", {"candles": candles(rally)}).action == "BUY"


def test_stop_loss_and_take_profit_exits():
    now = datetime.now(timezone.utc)
    state = RiskState(
        daily_loss_limit=Decimal("25"),
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
    )
    state.open_position(Decimal("100"), Decimal("25"), now)

    assert state.exit_reason(Decimal("99.5")) is None
    assert state.exit_reason(Decimal("98")) == "stop_loss"
    assert state.exit_reason(Decimal("104")) == "take_profit"


def test_daily_loss_limit_halts_and_resets_next_day():
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    state = RiskState(
        daily_loss_limit=Decimal("25"),
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
    )
    state.open_position(Decimal("100"), Decimal("1000"), now)
    realized = state.close_position(Decimal("97"), now)

    assert realized == Decimal("-30")
    assert state.halted(now)
    assert not state.halted(now + timedelta(days=1))


def test_closing_without_a_position_is_a_noop():
    now = datetime.now(timezone.utc)
    state = RiskState(
        daily_loss_limit=Decimal("25"),
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
    )
    assert state.close_position(Decimal("100"), now) == Decimal("0")
    assert state.snapshot(now)["position"] is None
