from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Ticker:
    """Normalized ticker across exchanges."""
    product_id: str
    price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume_24h: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Candle:
    """Normalized OHLCV candle across exchanges."""
    start: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
        }


@dataclass(frozen=True)
class ProductInfo:
    """Normalized product/trading-pair metadata."""
    product_id: str
    symbol: str
    price: Decimal
    quote_increment: Decimal = Decimal("0.01")
    base_increment: Decimal = Decimal("0.0001")
    quote_min_size: Decimal = Decimal("1")
    base_min_size: Decimal = Decimal("0.0001")


@dataclass(frozen=True)
class OrderResult:
    """Normalized order-submission result."""
    order_id: str
    product_id: str
    side: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


class ExchangeError(RuntimeError):
    """Raised when an exchange cannot complete a request."""


@runtime_checkable
class ExchangeClient(Protocol):
    """Protocol every exchange adapter must implement."""

    name: str

    @property
    def configured(self) -> bool: ...

    def to_product_id(self, pair: str) -> str:
        """Normalize a user-facing pair name to this exchange's product id."""
        ...

    def get_account(self) -> dict[str, Any]:
        """Return {"balances": {asset: amount}} with exchange-native asset keys."""
        ...

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        """Return {"pair", "price", "raw"} for a product."""
        ...

    def get_ohlc(self, product_id: str, interval: int = 5) -> list[dict[str, Any]]:
        """Return candle dicts: start/open/high/low/close/volume."""
        ...

    def get_market_snapshot(self, product_id: str) -> dict[str, Any]:
        """Return {"product": ProductInfo-as-dict, "candles": [...]}."""
        ...

    def add_market_order(
        self,
        *,
        pair: str,
        side: str,
        volume: str,
        volume_in_quote: bool = False,
        userref: str | None = None,
    ) -> dict[str, Any]:
        """Submit a market order; return exchange-native result dict."""
        ...


def normalize_product_id(product_id: str) -> str:
    """Exchange-agnostic product id normalization (Coinbase-style BASE-QUOTE)."""
    value = product_id.strip().upper()
    if "-" not in value and value.endswith("USD"):
        value = f"{value[:-3]}-USD"
    return value
