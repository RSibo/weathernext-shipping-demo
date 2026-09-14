#!/usr/bin/env bash
# Creates a Maps Platform API key restricted to routes.googleapis.com at creation time (never an
# unrestricted window). Prompts for confirmation since this is a real, if minor, secret-management
# surface.
set -euo pipefail

# Required, no fallback: a same-named default here would let anyone who forgets to set this
# silently target the original project instead of their own.
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your own GCP project (see .env.example)}"
DISPLAY_NAME="${DISPLAY_NAME:-weathernext-route-agent-routes-key}"

echo "About to create an API key on ${PROJECT_ID} restricted to routes.googleapis.com."
read -r -p "Continue? [y/N] " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

# `create` is a long-running operation; --format=value(response.name) waits for completion and
# extracts the Key resource name from the finished operation (not the operation's own name, which
# `value(name)` would incorrectly return).
KEY_RESOURCE="$(gcloud services api-keys create \
  --project="${PROJECT_ID}" \
  --display-name="${DISPLAY_NAME}" \
  --api-target=service=routes.googleapis.com \
  --format="value(response.name)")"

echo "Created key resource: ${KEY_RESOURCE}"

KEY_STRING="$(gcloud services api-keys get-key-string "${KEY_RESOURCE}" --format="value(keyString)")"

echo
echo "Key string retrieved (not printed above the fold to avoid accidental copy-paste of the wrong line)."
echo "Add this to your local .env (never commit it):"
echo
echo "GOOGLE_MAPS_ROUTES_API_KEY=${KEY_STRING}"
