from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from .api.routes import CODE_VERSION, register_routes
from .api.schemas import WebhookSignal  # re-exported for engine.scanner
from .config import settings
from .engine.portfolio import portfolio
from .engine.scanner import autonomous_loop
from .exchanges import coinbase_client, kraken_client
from .strategies import (
    LLMStrategy,
    MLStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    SignalAggregator,
)

__all__ = ["WebhookSignal", "app", "build_app", "build_aggregator"]


def build_aggregator() -> SignalAggregator:
    """Default per-scan strategy set: rule-based + LLM + ML ensemble."""
    return SignalAggregator(
        [
            MomentumStrategy(),
            MeanReversionStrategy(),
            LLMStrategy(),
            MLStrategy(),
        ],
        mode="weighted_vote",
    )


def select_exchange():
    """Pick the active exchange adapter from config."""
    if settings.active_exchange.lower() == "coinbase":
        if not settings.coinbase_enabled:
            raise RuntimeError(
                "ACTIVE_EXCHANGE=coinbase requires COINBASE_ENABLED=true "
                "with CDP credentials")
        return coinbase_client
    return kraken_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(
        autonomous_loop(select_exchange(), build_aggregator(), portfolio))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def build_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=CODE_VERSION,
        lifespan=lifespan,
    )
    register_routes(
        app,
        exchange=select_exchange(),
        aggregator=build_aggregator(),
        store=portfolio,
    )
    return app


app = build_app()
