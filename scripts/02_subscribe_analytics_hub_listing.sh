#!/usr/bin/env bash
# Subscribes to the WeatherNext 3 Analytics Hub listing, creating a linked dataset in your GCP
# project. Defaults below point at Google's own public WeatherNext 3 listing, but this still
# requires your GCP project to already have WeatherNext 3 access approved (allowlist-gated via
# Google's Data Request form -- see the README before assuming this is a quick step).
#
# There is no `gcloud`/`bq` subcommand for Analytics Hub subscription (confirmed: no
# `gcloud analytics-hub` or `gcloud alpha bigquery data-exchanges` command group exists as of this
# writing) -- this calls the REST API's `dataExchanges.listings.subscribe` method directly.
set -euo pipefail

# Required, no fallback: a same-named default here would let anyone who forgets to set this
# silently target the original project instead of their own.
DEST_PROJECT="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your own GCP project (see .env.example)}"
DEST_DATASET_ID="${WEATHERNEXT_DATASET:-weathernext_3}"
LOCATION="${BQ_LOCATION:-US}"

# Google's own WeatherNext 3 Analytics Hub listing (US region) — confirmed live and stable, not
# specific to this repo's original project (see scripts/01_discover_weathernext_listing.md).
# Override these only if Google publishes a different regional listing you need instead (e.g. EU).
PUBLISHER_PROJECT="${PUBLISHER_PROJECT:-866962084172}"
EXCHANGE_ID="${EXCHANGE_ID:-weathernext_19397e1bcb7}"
LISTING_ID="${LISTING_ID:-weathernext_3_1a067c1e929}"

echo "About to subscribe to:"
echo "  projects/${PUBLISHER_PROJECT}/locations/${LOCATION}/dataExchanges/${EXCHANGE_ID}/listings/${LISTING_ID}"
echo "This creates a linked dataset: ${DEST_PROJECT}:${DEST_DATASET_ID} (location ${LOCATION})."
read -r -p "Continue? [y/N] " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

TOKEN="$(gcloud auth print-access-token)"

curl -sS -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "https://analyticshub.googleapis.com/v1/projects/${PUBLISHER_PROJECT}/locations/${LOCATION}/dataExchanges/${EXCHANGE_ID}/listings/${LISTING_ID}:subscribe" \
  -d "{\"destinationDataset\": {\"datasetReference\": {\"projectId\": \"${DEST_PROJECT}\", \"datasetId\": \"${DEST_DATASET_ID}\"}, \"location\": \"${LOCATION}\"}}"

echo
echo "Subscribed. Verifying linked dataset..."
bq show --format=prettyjson "${DEST_PROJECT}:${DEST_DATASET_ID}"
bq ls "${DEST_PROJECT}:${DEST_DATASET_ID}"

echo
echo "Now run: bq show --schema --format=prettyjson ${DEST_PROJECT}:${DEST_DATASET_ID}.<TABLE>"
echo "and transcribe the real schema into scripts/01_discover_weathernext_listing.md and tools/weather.py."
