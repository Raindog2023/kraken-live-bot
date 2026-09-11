import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import main
from app.godmod3_client import Godmod3Analysis


def snapshot(closes):
    return {
        "product": {"symbol": "XBTUSD", "price": str(closes[-1])},
        "candles": [
            {
                "start": index * 300,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1,
            }
            for index, close in enumerate(closes)
        ],
        "candle_granularity": "FIVE_MINUTE",
    }


@pytest.fixture
def flat_market(monkeypatch):
    monkeypatch.setattr(
        main.kraken_client,
        "get_market_snapshot",
        lambda pair: snapshot([100 + index * 0.001 for index in range(60)]),
    )

    async def analyze(product_id, market_data):
        return Godmod3Analysis(
            product_id=product_id,
            action="BUY",
            confidence=99,
            rationale="llm is bullish",
        )

    monkeypatch.setattr(main.godmod3_client, "analyze", analyze)


def test_confident_signal_without_edge_is_rejected(flat_market):
    result = asyncio.run(main.execute_auto_trade("BTC-USD", Decimal("25"), 50))

    assert result["status"] == "below_cost_hurdle"
    assert result["submitted"] is False
    assert result["required_edge_pct"] == pytest.approx(1.12)


def test_daily_loss_limit_blocks_scanning(monkeypatch, flat_market):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(main.risk, "realized_pnl", Decimal("-30"))
    monkeypatch.setattr(main.risk, "day", now)

    result = asyncio.run(main.execute_auto_trade("BTC-USD", Decimal("25"), 50))

    assert result["status"] == "halted"
    assert result["reason"] == "daily_loss_limit"


def test_stop_loss_exits_without_calling_the_analyzer(monkeypatch):
    monkeypatch.setattr(
        main.kraken_client,
        "get_market_snapshot",
        lambda pair: snapshot([100.0] * 59 + [90.0]),
    )
    monkeypatch.setattr(
        main.kraken_client,
        "get_account",
        lambda: {"balances": {"XXBT": "1", "ZUSD": "1000"}},
    )
    monkeypatch.setattr(
        main.godmod3_client,
        "analyze",
        lambda *args, **kwargs: pytest.fail("analyzer must not run on a forced exit"),
    )
    main.risk.open_position(Decimal("100"), Decimal("25"), datetime.now(timezone.utc))

    try:
        result = asyncio.run(main.execute_auto_trade("BTC-USD", Decimal("25"), 50))
    finally:
        main.risk.position = None

    assert result["action"] == "SELL"
    assert "stop_loss" in result["rationale"]
    assert result["status"] == "dry_run"
