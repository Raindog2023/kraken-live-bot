from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Any

import httpx

from .config import settings


class KrakenError(RuntimeError):
    """Raised when Kraken cannot complete a request."""


def to_kraken_pair(product_id: str) -> str:
    value = product_id.strip().upper().replace("-", "").replace("/", "")
    if value in {"BTCUSD", "XBTUSD", "BTCUSDT"}:
        return "XBTUSD"
    if value.endswith("USD") and not value.startswith("XBT"):
        base = value[:-3]
        if base == "BTC":
            return "XBTUSD"
        return f"{base}USD"
    return value or settings.kraken_pair


class KrakenClient:
    def __init__(self) -> None:
        self.timeout = settings.request_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(settings.kraken_api_key and settings.kraken_api_secret)

    @property
    def base_url(self) -> str:
        return settings.kraken_base_url.rstrip("/")

    def _sign(self, url_path: str, data: dict[str, Any]) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(settings.kraken_api_secret)
        signature = hmac.new(secret, message, hashlib.sha512)
        return base64.b64encode(signature.digest()).decode()

    def _public(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/0/public/{method}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params or {})
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise KrakenError(f"Kraken public request failed: {exc}") from exc

        errors = payload.get("error") or []
        if errors:
            raise KrakenError(f"Kraken public error: {errors}")
        return payload.get("result")

    def _private(self, method: str, data: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise KrakenError("Kraken API credentials are not configured")

        url_path = f"/0/private/{method}"
        body = dict(data or {})
        body["nonce"] = str(int(time.time() * 1_000_000))
        headers = {
            "API-Key": settings.kraken_api_key.strip(),
            "API-Sign": self._sign(url_path, body),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}{url_path}",
                    headers=headers,
                    data=body,
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise KrakenError(f"Kraken private request failed: {exc}") from exc

        errors = payload.get("error") or []
        if errors:
            raise KrakenError(f"Kraken private error: {errors}")
        return payload.get("result")

    def get_account(self) -> dict[str, Any]:
        balance = self._private("Balance")
        if not isinstance(balance, dict):
            raise KrakenError("Kraken balance response was invalid")
        return {"balances": balance}

    def get_ticker(self, pair: str) -> dict[str, Any]:
        kraken_pair = to_kraken_pair(pair)
        result = self._public("Ticker", {"pair": kraken_pair})
        if not isinstance(result, dict) or not result:
            raise KrakenError("Kraken ticker response was invalid")
        first = next(iter(result.values()))
        last = None
        if isinstance(first, dict):
            last_list = first.get("c") or []
            if last_list:
                last = last_list[0]
        if not last:
            raise KrakenError("Kraken ticker had no last price")
        return {
            "pair": kraken_pair,
            "price": str(last),
            "raw": first,
        }

    def get_ohlc(self, pair: str, interval: int = 5) -> list[dict[str, Any]]:
        kraken_pair = to_kraken_pair(pair)
        result = self._public(
            "OHLC",
            {"pair": kraken_pair, "interval": interval},
        )
        if not isinstance(result, dict):
            return []
        series = None
        for key, value in result.items():
            if key != "last" and isinstance(value, list):
                series = value
                break
        candles: list[dict[str, Any]] = []
        if isinstance(series, list):
            for row in series[-60:]:
                if not isinstance(row, list) or len(row) < 7:
                    continue
                candles.append(
                    {
                        "start": row[0],
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[6],
                    }
                )
        return candles

    def get_market_snapshot(self, pair: str) -> dict[str, Any]:
        ticker = self.get_ticker(pair)
        candles = self.get_ohlc(pair, interval=5)
        return {
            "product": {
                "symbol": ticker["pair"],
                "price": ticker["price"],
                "quote_increment": "0.01",
                "base_increment": "0.0001",
                "quote_min_size": "1",
                "base_min_size": "0.0001",
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
        data: dict[str, Any] = {
            "pair": to_kraken_pair(pair),
            "type": side.lower(),
            "ordertype": "market",
            "volume": volume,
        }
        if volume_in_quote:
            data["oflags"] = "viqc"
        if userref:
            data["userref"] = abs(hash(userref)) % 2_147_483_647
        result = self._private("AddOrder", data)
        if not isinstance(result, dict):
            raise KrakenError("Kraken AddOrder response was invalid")
        return result


kraken_client = KrakenClient()
