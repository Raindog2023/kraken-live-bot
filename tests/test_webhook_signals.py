from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import register_routes
from app.api.schemas import ExternalSignal
from app.config import settings
from app.engine.portfolio import Portfolio

SECRET = "test-secret"


class StubExchange:
    name = "kraken"
    configured = True

    def to_product_id(self, pair):
        return pair.replace("-", "")

    def get_account(self):
        return {"balances": {"ZUSD": "10000", "XXBT": "1.5"}}

    def get_market_snapshot(self, product_id):
        return {
            "product": {
                "product_id": product_id,
                "price": "50000",
                "quote_increment": "0.01",
                "base_increment": "0.0001",
            },
            "candles": [],
        }


class StubAggregator:
    strategies: list = []


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", SECRET)
    monkeypatch.setattr(settings, "live_trading", False)
    monkeypatch.setattr(settings, "paper_trading", True)
    monkeypatch.setattr(settings, "paused", False)
    monkeypatch.setattr(settings, "emergency_stop", False)
    monkeypatch.setattr(settings, "ml_kill_switch", False)
    monkeypatch.setattr(settings, "webhook_signals_enabled", True)
    monkeypatch.setattr(settings, "webhook_min_confidence", 0)

    app = FastAPI()
    register_routes(
        app,
        exchange=StubExchange(),
        aggregator=StubAggregator(),
        store=Portfolio(tmp_path / "portfolio.db"),
    )
    return TestClient(app)


def post(client, payload):
    return client.post(
        "/webhook", json=payload, headers={"X-Webhook-Secret": SECRET})


def test_maps_provider_aliases():
    signal = ExternalSignal.model_validate({
        "id": "s8-1",
        "ticker": "BTCUSD",
        "side": "long",
        "size": "75",
        "score": 0.82,
        "source": "signal8",
    })
    assert signal.signal_id == "s8-1"
    assert signal.product_id == "BTCUSD"
    assert signal.action == "BUY"
    assert signal.quote_amount == Decimal("75")
    assert signal.confidence == 82
    assert signal.strategy == "signal8"


def test_maps_nested_payload_and_percent_confidence():
    signal = ExternalSignal.model_validate({
        "alert_id": "s8-2",
        "data": {"symbol": "ETH-USD", "direction": "SHORT", "confidence": 64},
    })
    assert signal.product_id == "ETH-USD"
    assert signal.action == "SELL"
    assert signal.confidence == 64
    assert signal.quote_amount is None


def test_unsupported_action_rejected():
    with pytest.raises(ValueError):
        ExternalSignal.model_validate({"symbol": "BTC-USD", "side": "wiggle"})


def test_requires_secret(client):
    response = client.post("/webhook", json={"symbol": "BTC-USD",
                                             "side": "buy"})
    assert response.status_code == 401


def test_paper_fill_and_idempotency(client):
    payload = {"signal_id": "s8-100", "ticker": "BTCUSD", "action": "buy",
               "quote_amount": "60"}
    first = post(client, payload).json()
    assert first["status"] == "paper_filled"
    assert first["product_id"] == "BTC-USD"
    assert first["quote_amount"] == "60.00"

    second = post(client, payload).json()
    assert second["status"] == "duplicate"


def test_default_quote_applied(client, monkeypatch):
    monkeypatch.setattr(settings, "webhook_default_quote", 40.0)
    body = post(client, {"signal_id": "s8-101", "symbol": "BTC-USD",
                         "side": "sell"}).json()
    assert body["quote_amount"] == "40.00"


def test_quote_capped_at_max_order_quote(client, monkeypatch):
    monkeypatch.setattr(settings, "max_order_quote", 100.0)
    body = post(client, {"signal_id": "s8-102", "symbol": "BTC-USD",
                         "side": "buy", "amount": "5000"}).json()
    assert body["quote_amount"] == "100.00"


def test_hold_is_ignored(client):
    body = post(client, {"signal_id": "s8-103", "symbol": "BTC-USD",
                         "side": "flat"}).json()
    assert body["status"] == "ignored"


def test_low_confidence_skipped(client, monkeypatch):
    monkeypatch.setattr(settings, "webhook_min_confidence", 70)
    body = post(client, {"signal_id": "s8-104", "symbol": "BTC-USD",
                         "side": "buy", "confidence": 55}).json()
    assert body["status"] == "low_confidence"


def test_kill_switch_blocks(client, monkeypatch):
    monkeypatch.setattr(settings, "emergency_stop", True)
    body = post(client, {"signal_id": "s8-105", "symbol": "BTC-USD",
                         "side": "buy"}).json()
    assert body["status"] == "blocked"
    assert body["reason"] == "emergency_stop"
