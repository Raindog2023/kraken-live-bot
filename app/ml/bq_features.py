"""BigQuery feature source — lets the ML strategy consume pipeline-computed
feature rows instead of computing from REST candles locally.

Enable with BQ_FEATURES_ENABLED=true plus GCP_PROJECT_ID (and ADC or a
service-account key). Falls back to None on any error so callers can keep
the local feature path.
"""
from __future__ import annotations

from typing import Any

from ..config import settings
from .features import FEATURE_COLUMNS


def latest_feature_row(
    product_id: str,
    exchange: str = "kraken",
    *,
    max_age_seconds: int = 600,
) -> dict[str, float] | None:
    """Return the newest kraken_market.features row as a FEATURE_COLUMNS dict.

    None when disabled, unconfigured, stale, or on any BigQuery error.
    """
    if not getattr(settings, "bq_features_enabled", False):
        return None
    project = getattr(settings, "gcp_project_id", "")
    if not project:
        return None

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        dataset = getattr(settings, "bq_dataset", "kraken_market")
        cols = ", ".join(FEATURE_COLUMNS)
        query = f"""
            SELECT {cols}, feature_ts
            FROM `{project}.{dataset}.features`
            WHERE exchange = @exchange AND product_id = @product
            ORDER BY feature_ts DESC
            LIMIT 1
        """
        job = client.query(query, job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "exchange", "STRING", exchange),
                bigquery.ScalarQueryParameter(
                    "product", "STRING", product_id),
            ]))
        row = next(iter(job.result()), None)
        if row is None:
            return None

        import datetime
        age = (datetime.datetime.now(datetime.timezone.utc)
               - row["feature_ts"]).total_seconds()
        if age > max_age_seconds:
            return None

        out = {c: float(row[c]) for c in FEATURE_COLUMNS}
        out["timestamp"] = row["feature_ts"].timestamp()
        return out
    except Exception:
        return None
