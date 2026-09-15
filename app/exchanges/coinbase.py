from __future__ import annotations

import secrets
import time
from typing import Any

import httpx

from ..config import settings
from .base import ExchangeError, normalize_product_id

_CDP_HOST = "api.coinbase.com"
_REST_PREFIX = "/api/v3/brokerage"


class CoinbaseError(ExchangeError):
    """Raised when Coinbase Advanced Trade cannot complete a request."""


def to_coinbase_product(product_id: str) -> str:
    """Map a pair name to Coinbase product id form (BASE-QUOTE).

    Accepts BTC-USD, BTCUSD, XBTUSD, BTC/USD -> BTC-USD.
    """
    value = product_id.strip().upper().replace("/", "-")
    if "-" in value:
        base, _, quote = value.partition("-")
        if base == "XBT":
            base = "BTC"
        return f"{base}-{quote}"
    if value.endswith("USDT"):
        return f"{value[:-4]}-USDT"
    if value.endswith("USD"):
        base = value[:-3]
        if base == "XBT":
            base = "BTC"
        return f"{base}-USD"
    return normalize_product_id(value)


def _build_jwt(method: str, path: str) -> str:
    """Build a per-request ES256 JWT for Coinbase CDP auth.

    Header: {alg: ES256, kid: key_name, nonce, typ: JWT}
    Claims: {sub: key_name, iss: 'cdp', nbf, exp, uri: 'METHOD host/path'}
    """
    import jwt  # PyJWT, optional dependency

    key_name = (settings.coinbase_api_key_name or "").strip()
    private_key_pem = (settings.coinbase_private_key or "").strip()
    if not key_name or not private_key_pem:
        raise CoinbaseError("Coinbase CDP credentials are not configured")

    uri = f"{method} {_CDP_HOST}{path}"
    now = int(time.time())
    try:
        token = jwt.encode(
            {
                "sub": key_name,
                "iss": "cdp",
                "nbf": now,
                "exp": now + 120,
                "uri": uri,
            },
            private_key_pem,
            algorithm="ES256",
            headers={
                "kid": key_name,
                "nonce": secrets.token_hex(16),
                "typ": "JWT",
            },
        )
    except Exception as exc:
        raise CoinbaseError(f"Coinbase JWT build failed: {exc}") from exc
    return token


class CoinbaseClient:
    """Coinbase Advanced Trade REST client implementing ExchangeClient."""

    name = "coinbase"

    def __init__(self) -> None:
        self.timeout = settings.request_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(
            (settings.coinbase_api_key_name or "").strip()
            and (settings.coinbase_private_key or "").strip())

    @property
    def base_url(self) -> str:
        return (
            (settings.coinbase_base_url or f"https://{_CDP_HOST}")
            .strip().rstrip("/"))

    def to_product_id(self, pair: str) -> str:
        return to_coinbase_product(pair)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {_build_jwt(method, path)}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method, url, params=params, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
        except ExchangeError:
            raise
        except Exception as exc:
            raise CoinbaseError(
                f"Coinbase {method} {path} failed: {exc}") from exc

    def get_account(self) -> dict[str, Any]:
        result = self._request("GET", f"{_REST_PREFIX}/accounts",
                               params={"limit": 250})
        accounts = result.get("accounts") if isinstance(result, dict) else None
        if not isinstance(accounts, list):
            raise CoinbaseError("Coinbase accounts response was invalid")
        balances: dict[str, Any] = {}
        for account in accounts:
            if not isinstance(account, dict):
                continue
            currency = account.get("currency")
            available = (account.get("available_balance") or {}).get("value")
            if currency and available is not None:
                balances[str(currency)] = available
        return {"balances": balances}

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        pid = to_coinbase_product(product_id)
        result = self._request(
            "GET", f"{_REST_PREFIX}/products/{pid}/ticker",
            params={"limit": 1}, auth=False)
        if not isinstance(result, dict):
            raise CoinbaseError("Coinbase ticker response was invalid")
        price = result.get("price")
        if price is None:
            trades = result.get("trades") or []
            if trades and isinstance(trades[0], dict):
                price = trades[0].get("price")
        if price is None:
            raise CoinbaseError("Coinbase ticker had no last price")
        return {"pair": pid, "price": str(price), "raw": result}

    def get_ohlc(
        self, product_id: str, interval: int = 5
    ) -> list[dict[str, Any]]:
        pid = to_coinbase_product(product_id)
        granularity = {
            1: "ONE_MINUTE", 5: "FIVE_MINUTE", 15: "FIFTEEN_MINUTE",
            30: "THIRTY_MINUTE", 60: "ONE_HOUR", 120: "TWO_HOUR",
            360: "SIX_HOUR", 1440: "ONE_DAY",
        }.get(interval, "FIVE_MINUTE")
        end = int(time.time())
        span = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900,
                "THIRTY_MINUTE": 1800, "ONE_HOUR": 3600, "TWO_HOUR": 7200,
                "SIX_HOUR": 21600, "ONE_DAY": 86400}[granularity]
        result = self._request(
            "GET", f"{_REST_PREFIX}/products/{pid}/candles",
            params={
                "start": end - span * 70,
                "end": end,
                "granularity": granularity,
            },
            auth=False,
        )
        raw = result.get("candles") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            return []
        candles: list[dict[str, Any]] = []
        for row in raw[-60:]:
            if not isinstance(row, dict):
                continue
            candles.append({
                "start": int(row.get("start", 0)),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
            })
        # Coinbase returns newest-first; normalize to oldest-first like Kraken.
        candles.sort(key=lambda c: c["start"])
        return candles

    def get_multi_timeframe_data(self, product_id: str) -> dict[str, Any]:
        return {
            "5m": self.get_ohlc(product_id, interval=5),
            "15m": self.get_ohlc(product_id, interval=15),
            "1h": self.get_ohlc(product_id, interval=60),
        }

    def get_market_snapshot(self, product_id: str) -> dict[str, Any]:
        pid = to_coinbase_product(product_id)
        ticker = self.get_ticker(pid)
        candles = self.get_ohlc(pid, interval=5)
        return {
            "product": {
                "symbol": pid,
                "price": ticker["price"],
                "quote_increment": "0.01",
                "base_increment": "0.00000001",
                "quote_min_size": "1",
                "base_min_size": "0.00000001",
            },
            "candles": candles,
            "candle_granularity": "FIVE_MINUTE",
        }

    def add_market_order(
        self,
        *,
        pair: str,
        side: str,
        volume: str,
        volume_in_quote: bool = False,
        userref: str | None = None,
    ) -> dict[str, Any]:
        pid = to_coinbase_product(pair)
        client_order_id = userref or f"cb-{int(time.time()*1000)}"
        order_config: dict[str, Any]
        if side.lower() == "buy":
            order_config = {
                "market_market_ioc": {"quote_size": str(volume)}}
        else:
            if volume_in_quote:
                ticker = self.get_ticker(pid)
                price = float(ticker["price"])
                if price <= 0:
                    raise CoinbaseError("Coinbase ticker price is invalid")
                volume = f"{(float(volume) / price):.8f}"
            order_config = {
                "market_market_ioc": {"base_size": str(volume)}}
        body = {
            "client_order_id": client_order_id,
            "product_id": pid,
            "side": side.upper(),
            "order_configuration": order_config,
        }
        result = self._request("POST", f"{_REST_PREFIX}/orders", body=body)
        if not isinstance(result, dict):
            raise CoinbaseError("Coinbase order response was invalid")
        if not result.get("success", False):
            raise CoinbaseError(
                f"Coinbase order rejected: {result.get('error_response', result)}")
        return {
            "order_id": (result.get("success_response") or {}).get("order_id"),
            "client_order_id": client_order_id,
            "txid": (result.get("success_response") or {}).get("order_id"),
            "raw": result,
        }


coinbase_client = CoinbaseClient()
