import asyncio
from decimal import Decimal

import pytest

from app import main
from tests.test_auto_trade import snapshot


@pytest.fixture
def market(monkeypatch):
    monkeypatch.setattr(
        main.kraken_client,
        "get_market_snapshot",
        lambda pair: snapshot([100.0] * 60),
    )

    async def never(*args, **kwargs):
        pytest.fail("accumulation must not spend money on analysis")

    monkeypatch.setattr(main.godmod3_client, "analyze", never)


def test_accumulation_buys_flat_tape_without_any_signal(monkeypatch, market):
    """No momentum, no LLM, no trend — the schedule is the whole strategy."""
    monkeypatch.setattr(
        main.kraken_client,
        "get_account",
        lambda: {"balances": {"ZUSD": "1000"}},
    )

    result = asyncio.run(main.execute_accumulation("BTC-USD", Decimal("25")))

    assert result["status"] == "dry_run"
    assert result["order"]["side"] == "BUY"
    assert Decimal(result["order"]["quote_size"]) == Decimal("25")


def test_accumulation_is_capped_by_the_cash_on_hand(monkeypatch, market):
    monkeypatch.setattr(
        main.kraken_client,
        "get_account",
        lambda: {"balances": {"ZUSD": "10"}},
    )

    result = asyncio.run(main.execute_accumulation("BTC-USD", Decimal("25")))

    assert Decimal(result["order"]["quote_size"]) == Decimal("9.60")


def test_accumulation_stops_when_cash_is_below_the_minimum(monkeypatch, market):
    monkeypatch.setattr(
        main.kraken_client,
        "get_account",
        lambda: {"balances": {"ZUSD": "2"}},
    )

    result = asyncio.run(main.execute_accumulation("BTC-USD", Decimal("25")))

    assert result["status"] == "insufficient_funds"
    assert result["submitted"] is False


def test_accumulation_submits_only_when_live_trading_is_on(monkeypatch, market):
    monkeypatch.setattr(
        main.kraken_client,
        "get_account",
        lambda: {"balances": {"ZUSD": "1000"}},
    )
    monkeypatch.setattr(main.settings, "live_trading", True)
    submitted: list[dict] = []
    monkeypatch.setattr(
        main,
        "submit_kraken_order",
        lambda order: submitted.append(order) or {"txid": ["OK"]},
    )

    result = asyncio.run(main.execute_accumulation("BTC-USD", Decimal("25")))

    assert result["status"] == "submitted"
    assert submitted[0]["side"] == "BUY"


def test_accumulation_never_sells_when_price_falls(monkeypatch, market):
    """A stop-loss would realize the dip; accumulation rides it by design."""
    monkeypatch.setattr(
        main.kraken_client,
        "get_account",
        lambda: {"balances": {"ZUSD": "1000", "XXBT": "1"}},
    )
    monkeypatch.setattr(
        main.kraken_client,
        "get_market_snapshot",
        lambda pair: snapshot([100.0] * 59 + [50.0]),
    )

    result = asyncio.run(main.execute_accumulation("BTC-USD", Decimal("25")))

    assert result["action"] == "BUY"
