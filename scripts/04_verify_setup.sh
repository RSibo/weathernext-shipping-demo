#!/usr/bin/env bash
# Read-only smoke test of Day-1 infra. Safe/idempotent to re-run at any time (the only live call
# is one cheap Routes API request). Loads .env if present.
set -uo pipefail

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

PROJECT_ID="${GCP_PROJECT_ID:-weathernext3-route-agent}"
WEATHERNEXT_DATASET="${WEATHERNEXT_DATASET:-weathernext3}"
WEATHERNEXT_TABLE="${WEATHERNEXT_TABLE:-weathernext_3_0_0_0p1deg}"

pass=0
fail=0

check() {
  local desc="$1"
  shift
  if "$@" >/tmp/verify_setup_out.$$ 2>&1; then
    echo "OK   - ${desc}"
    pass=$((pass + 1))
  else
    echo "FAIL - ${desc}"
    sed 's/^/       /' /tmp/verify_setup_out.$$
    fail=$((fail + 1))
  fi
  rm -f /tmp/verify_setup_out.$$
}

check "routes.googleapis.com enabled on ${PROJECT_ID}" \
  bash -c "gcloud services list --enabled --project=${PROJECT_ID} --filter='name:routes.googleapis.com' --format='value(name)' | grep -q routes.googleapis.com"

check "WeatherNext dataset ${PROJECT_ID}:${WEATHERNEXT_DATASET} exists" \
  bash -c "bq show --format=none ${PROJECT_ID}:${WEATHERNEXT_DATASET}"

check "WeatherNext table ${WEATHERNEXT_DATASET}.${WEATHERNEXT_TABLE} is queryable (dry run)" \
  bash -c "bq query --project_id=${PROJECT_ID} --use_legacy_sql=false --dry_run 'SELECT 1 FROM \`${PROJECT_ID}.${WEATHERNEXT_DATASET}.${WEATHERNEXT_TABLE}\` LIMIT 1'"

if [[ -z "${GOOGLE_MAPS_ROUTES_API_KEY:-}" ]]; then
  echo "FAIL - GOOGLE_MAPS_ROUTES_API_KEY is not set (see .env.example)"
  fail=$((fail + 1))
else
  check "Maps Routes API key works (SF -> Oakland)" \
    bash -c "curl -sS -f -X POST -H 'Content-Type: application/json' -H \"X-Goog-Api-Key: \${GOOGLE_MAPS_ROUTES_API_KEY}\" -H 'X-Goog-FieldMask: routes.duration,routes.distanceMeters' -d '{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.8044,\"longitude\":-122.2711}}},\"travelMode\":\"DRIVE\"}' https://routes.googleapis.com/directions/v2:computeRoutes | grep -q distanceMeters"
fi

echo
echo "${pass} passed, ${fail} failed."
[[ "${fail}" -eq 0 ]]
