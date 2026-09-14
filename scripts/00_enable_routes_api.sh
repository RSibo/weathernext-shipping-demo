#!/usr/bin/env bash
# Enables the Routes API on the target project. Cheap and fully reversible
# (gcloud services disable routes.googleapis.com --project=$PROJECT_ID).
set -euo pipefail

# Required, no fallback: a same-named default here would let anyone who forgets to set this
# silently target the original project instead of their own. Set GCP_PROJECT_ID in your .env or
# export it before running this script.
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your own GCP project (see .env.example)}"

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
