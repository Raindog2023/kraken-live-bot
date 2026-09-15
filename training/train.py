"""Walk-forward training against BigQuery features -> GCS artifact.

Pulls labeled feature rows from kraken_market.features, runs the same
chronological walk-forward evaluation as app.ml.model.train_walk_forward,
then trains a final artifact on all data and uploads it to GCS.

Usage:
    python training/train.py --project $GCP_PROJECT_ID \
        --product BTC-USD --exchange kraken \
        --out gs://$BUCKET/models/ml_model.joblib
"""
from __future__ import annotations

import argparse
import sys
import tempfile

from google.cloud import bigquery, storage

sys.path.insert(0, ".")
from app.ml.features import FEATURE_COLUMNS  # noqa: E402
from app.ml.model import build_pipeline, save_artifact, train_walk_forward  # noqa: E402


def fetch_labeled_features(client: bigquery.Client, project: str,
                           dataset: str, product: str,
                           exchange: str) -> tuple[list[dict], list[int]]:
    cols = ", ".join(FEATURE_COLUMNS)
    query = f"""
        SELECT {cols}, label_h3
        FROM `{project}.{dataset}.features`
        WHERE exchange = @exchange AND product_id = @product
          AND label_h3 IS NOT NULL
        ORDER BY feature_ts
    """
    job = client.query(query, job_config=bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("exchange", "STRING", exchange),
            bigquery.ScalarQueryParameter("product", "STRING", product),
        ]))
    X, y = [], []
    for r in job.result():
        X.append({c: float(r[c]) for c in FEATURE_COLUMNS})
        y.append(int(r["label_h3"]))
    return X, y


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="kraken_market")
    parser.add_argument("--exchange", default="kraken")
    parser.add_argument("--product", default="BTC-USD")
    parser.add_argument("--out", required=True,
                        help="gs://bucket/path for the model artifact")
    parser.add_argument("--min-train", type=int, default=50)
    args = parser.parse_args()

    client = bigquery.Client(project=args.project)
    X, y = fetch_labeled_features(
        client, args.project, args.dataset, args.product, args.exchange)
    print(f"{len(X)} labeled rows for {args.exchange}:{args.product}")
    if len(X) < args.min_train + 10:
        raise SystemExit("not enough labeled rows to train")

    metrics = train_walk_forward(X, y, min_train=args.min_train)
    print(f"walk-forward: acc={metrics['accuracy']:.3f} "
          f"samples={metrics['samples']}")

    model = build_pipeline().fit(
        [[r[c] for c in FEATURE_COLUMNS] for r in X], y)

    with tempfile.NamedTemporaryFile(suffix=".joblib") as tmp:
        save_artifact(model, tmp.name)
        bucket_name, _, blob_name = args.out.removeprefix("gs://").partition("/")
        storage.Client(project=args.project).bucket(bucket_name).blob(
            blob_name).upload_from_filename(tmp.name)
    print(f"artifact -> {args.out} "
          f"(walk-forward acc={metrics['accuracy']:.3f})")


if __name__ == "__main__":
    main()
