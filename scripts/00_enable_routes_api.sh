#!/usr/bin/env bash
# Enables the Routes API on the target project. Cheap and fully reversible
# (gcloud services disable routes.googleapis.com --project=$PROJECT_ID).
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-weathernext3-route-agent}"

echo "Enabling routes.googleapis.com on ${PROJECT_ID}..."
gcloud services enable routes.googleapis.com --project="${PROJECT_ID}"

echo "Verifying..."
if gcloud services list --enabled --project="${PROJECT_ID}" \
    --filter="name:routes.googleapis.com" --format="value(name)" | grep -q routes.googleapis.com; then
  echo "OK: routes.googleapis.com is enabled on ${PROJECT_ID}."
else
  echo "FAILED: routes.googleapis.com is not enabled on ${PROJECT_ID}." >&2
  exit 1
fi
