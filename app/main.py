from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from .api.routes import CODE_VERSION, register_routes
from .api.schemas import WebhookSignal  # re-exported for engine.scanner
from .config import settings
from .engine.portfolio import portfolio
from .engine.scanner import autonomous_loop
from .exchanges import kraken_client
from .strategies import (
    LLMStrategy,
    MLStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    SignalAggregator,
)

__all__ = ["WebhookSignal", "app", "build_app"]


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    exchange = kraken_client
    aggregator = build_aggregator()
    task = asyncio.create_task(
        autonomous_loop(exchange, aggregator, portfolio))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def build_app() -> FastAPI:
    exchange = kraken_client
    aggregator = build_aggregator()
    app = FastAPI(
        title=settings.app_name,
        version=CODE_VERSION,
        lifespan=lifespan,
    )
    register_routes(app, exchange=exchange, aggregator=aggregator,
                    store=portfolio)
    return app


app = build_app()
