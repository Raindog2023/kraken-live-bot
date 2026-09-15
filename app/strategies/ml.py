from __future__ import annotations

from typing import Any

from ..config import settings
from .base import Signal


class MLStrategy:
    """Wraps the walk-forward ensemble model as a pluggable strategy.

    Requires a fresh, schema-valid artifact and completed OHLCV candles.
    Degrades to HOLD when the artifact is missing/stale.
    """

    name = "ml"
    weight = 1.2
    min_confidence = 60
    enabled = True

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
        multi_timeframe: dict[str, Any] | None = None,
    ) -> Signal:
        if not settings.ml_enabled or settings.ml_kill_switch:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=0,
                rationale="ml_disabled_or_killed",
            )

        try:
            from ..ml.features import normalize_completed_ohlcv, make_features
            from ..ml.model import load_artifact, predict

            # Prefer pipeline-computed features when the BQ source is enabled.
            features = None
            if getattr(settings, "bq_features_enabled", False):
                from ..ml.bq_features import latest_feature_row
                features = latest_feature_row(product_id, "kraken")

            if features is None:
                if not market_data.get("ohlcv"):
                    return Signal(
                        strategy=self.name,
                        product_id=product_id,
                        action="HOLD",
                        confidence=0,
                        rationale="no_ohlcv_data",
                    )
                candles = normalize_completed_ohlcv(market_data["ohlcv"])
                feature_rows = make_features(candles)
                if not feature_rows:
                    return Signal(
                        strategy=self.name,
                        product_id=product_id,
                        action="HOLD",
                        confidence=0,
                        rationale="insufficient_feature_rows",
                    )
                features = feature_rows[-1]

            prediction = predict(
                load_artifact(settings.ml_model_path),
                features,
                threshold=settings.ml_confidence_threshold,
            )
        except (OSError, ValueError, KeyError, ImportError) as exc:
            return Signal(
                strategy=self.name,
                product_id=product_id,
                action="HOLD",
                confidence=0,
                rationale=f"ml_unavailable: {exc}",
                meta={"error": str(exc)},
            )

        return Signal(
            strategy=self.name,
            product_id=product_id,
            action=prediction.signal,
            confidence=round(prediction.confidence * 100),
            rationale=prediction.reason,
            meta={"model_confidence": prediction.confidence},
        )
