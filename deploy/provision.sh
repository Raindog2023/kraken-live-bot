#!/usr/bin/env bash
# Provision the minimal GCP footprint for the market-data pipeline.
# Usage: ./deploy/provision.sh <PROJECT_ID> [REGION]
# Requires: gcloud auth login + billing already enabled on the project.
set -euo pipefail

PROJECT_ID="${1:?usage: provision.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
DATASET="kraken_market"
TICKS_TOPIC="market-ticks"
CANDLES_TOPIC="market-candles"
SUBSCRIPTION="market-candles-dataflow"
BUCKET="${PROJECT_ID}-kraken-bot"

echo "== project: ${PROJECT_ID} region: ${REGION} =="
gcloud config set project "${PROJECT_ID}"

# ---- APIs ---------------------------------------------------------------
gcloud services enable \
    pubsub.googleapis.com \
    dataflow.googleapis.com \
    bigquery.googleapis.com \
    secretmanager.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    storage.googleapis.com

# ---- Service accounts ---------------------------------------------------
for sa in kraken-ingest dataflow-worker kraken-trainer; do
    gcloud iam service-accounts create "${sa}" \
        --display-name="kraken-bot ${sa}" 2>/dev/null || true
done

INGEST_SA="kraken-ingest@${PROJECT_ID}.iam.gserviceaccount.com"
DF_SA="dataflow-worker@${PROJECT_ID}.iam.gserviceaccount.com"
TRAIN_SA="kraken-trainer@${PROJECT_ID}.iam.gserviceaccount.com"

# ingest publishes to Pub/Sub
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${INGEST_SA}" \
    --role="roles/pubsub.publisher" --quiet

# dataflow worker: bq read/write, gcs, pubsub subscriber
for role in roles/dataflow.worker roles/bigquery.dataEditor \
            roles/bigquery.jobUser roles/storage.objectAdmin \
            roles/pubsub.subscriber; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${DF_SA}" --role="${role}" --quiet
done

# trainer: bq read, gcs write for model artifacts
for role in roles/bigquery.dataViewer roles/bigquery.jobUser \
            roles/storage.objectAdmin; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${TRAIN_SA}" --role="${role}" --quiet
done

# ---- Pub/Sub ------------------------------------------------------------
gcloud pubsub topics create "${TICKS_TOPIC}"   2>/dev/null || true
gcloud pubsub topics create "${CANDLES_TOPIC}" 2>/dev/null || true
gcloud pubsub subscriptions create "${SUBSCRIPTION}" \
    --topic="${CANDLES_TOPIC}" \
    --ack-deadline=60 \
    --expiration-period=never 2>/dev/null || true

# ---- GCS staging / artifacts -------------------------------------------
gcloud storage buckets create "gs://${BUCKET}" \
    --location="${REGION}" --uniform-bucket-level-access 2>/dev/null || true
gcloud storage buckets create "gs://${BUCKET}-dataflow-tmp" \
    --location="${REGION}" --uniform-bucket-level-access 2>/dev/null || true

# ---- BigQuery -----------------------------------------------------------
bq mk --location="${REGION}" --dataset "${PROJECT_ID}:${DATASET}" 2>/dev/null || true
bq query --use_legacy_sql=false < "$(dirname "$0")/bigquery_schema.sql"

# ---- Budget guardrail ---------------------------------------------------
echo ""
echo "NEXT STEPS (manual):"
echo "  1. Create a billing budget alert (~\$50/mo):"
echo "     gcloud billing budgets create --billing-account=<ACCOUNT_ID> \\"
echo "       --display-name=kraken-bot-budget --budget-amount=50USD \\"
echo "       --filter-projects=projects/${PROJECT_ID}"
echo "  2. Store exchange keys in Secret Manager:"
echo "     gcloud secrets create kraken-api-key    --data-file=-"
echo "     gcloud secrets create coinbase-api-key  --data-file=-"
echo "  3. Build the Dataflow flex template:"
echo "     gcloud builds submit --config deploy/cloudbuild.yaml \\"
echo "       --substitutions=_REGION=${REGION},_BUCKET=${BUCKET}"
echo "DONE."
