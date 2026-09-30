#!/usr/bin/env bash
# Deploys the showcase as one public Cloud Run service. `gcloud run deploy --source .` uploads what
# .gcloudignore allows and Cloud Build builds the root Dockerfile. Idempotent: the first deploy and
# every redeploy are the same command. Adapted from genai-showcase-search/scripts/deploy.sh.
#
#   ./scripts/deploy.sh                 # setup checks, build, deploy, smoke test (one live run)
#   SKIP_SETUP=1 ./scripts/deploy.sh    # rebuild and redeploy only
#
# Reads these from the environment, falling back to .env. Values are never echoed.
#   GCP_PROJECT_ID              required, no default (same rule as the other scripts here)
#   GOOGLE_MAPS_ROUTES_API_KEY  stored in Secret Manager; the service reads it as a secret, so it is
#                               never an env var, never in the image, never in /api/config.
#                               Without it the service answers from recorded routes only.
#   MAPS_JS_API_KEY             browser key. Public by design, so this script restricts it to the
#                               service URL + localhost and to the Maps JavaScript API.
#   MAP_ID, GEMINI_MODEL, VERTEX_LOCATION   optional
#
# Public without allUsers: --no-invoker-iam-check switches the invoker check off, so the org's
# domain-restricted-sharing policy never comes into play. Abuse and cost are bounded by the app
# (per-instance run semaphore, daily live-run cap, then recorded runs) and by --max-instances.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Parsed rather than sourced: a .env value may contain characters the shell would interpret.
env_or_dotenv() {
  local name="$1"
  if [ -n "${!name:-}" ]; then printf '%s' "${!name}"; return; fi
  [ -f .env ] || return 0
  # `|| true`: an unset name is not an error (and `X="$(...)"` would otherwise trip set -e).
  grep -E "^${name}=" .env | tail -n1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//" || true
}
step() { printf '\n==> %s\n' "$*"; }

PROJECT_ID="$(env_or_dotenv GCP_PROJECT_ID)"
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "your-gcp-project-id" ]; then
  echo "error: set GCP_PROJECT_ID (environment or .env) to your own project" >&2
  exit 1
fi
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-weathernext-route-agent}"
ROUTES_SECRET="${ROUTES_SECRET:-routes-key}"
RUN_SA="${RUN_SA_NAME:-route-agent-run}@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA="${BUILD_SA_NAME:-route-agent-build}@${PROJECT_ID}.iam.gserviceaccount.com"
# Dev origins the browser key keeps working on, besides the service itself.
DEV_REFERRERS="${DEV_REFERRERS:-http://localhost:3000/*,http://localhost:8000/*}"

ROUTES_KEY="$(env_or_dotenv GOOGLE_MAPS_ROUTES_API_KEY)"
MAPS_JS_KEY="$(env_or_dotenv MAPS_JS_API_KEY)"

if ! gcloud auth print-access-token >/dev/null 2>&1; then
  echo "error: gcloud has no valid login. Run: gcloud auth login" >&2
  exit 1
fi
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
# Cloud Run's deterministic URL, known before the first deploy. Put this one on the QR code.
URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

echo "Deploying '$SERVICE' to $PROJECT_ID ($REGION)"
echo "  URL         $URL"
echo "  runtime SA  $RUN_SA"
echo "  build SA    $BUILD_SA"
echo "  Routes key  $([ -n "$ROUTES_KEY" ] && echo "Secret Manager: $ROUTES_SECRET" || echo "none (recorded routes only)")"

# --- One-time setup (every step is a no-op when already done) -----------------------------------
# Newly created service accounts take a few seconds to become bindable.
grant() {
  local member="$1" role="$2" i
  for i in 1 2 3 4 5 6; do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$member" \
      --role "$role" --condition None --quiet >/dev/null 2>&1; then return 0; fi
    sleep 5
  done
  echo "error: could not grant $role to $member" >&2
  return 1
}
ensure_sa() {
  gcloud iam service-accounts describe "$1" --project "$PROJECT_ID" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "${1%%@*}" --display-name "$2" --project "$PROJECT_ID"
}

if [ -z "${SKIP_SETUP:-}" ]; then
  step "APIs"
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com aiplatform.googleapis.com apikeys.googleapis.com \
    billingbudgets.googleapis.com --project "$PROJECT_ID"

  step "Service accounts"
  ensure_sa "$RUN_SA" "Route agent: Cloud Run runtime"
  ensure_sa "$BUILD_SA" "Route agent: Cloud Build"
  # Runtime: Gemini on Vertex AI, and logs. The replay forecast is baked into the image, so the
  # service never queries BigQuery and gets no BigQuery role.
  grant "$RUN_SA" roles/aiplatform.user
  grant "$RUN_SA" roles/logging.logWriter
  # Build: read the uploaded source, push the image, write build logs. Without a build SA,
  # --source builds run as the Compute Engine default account, which has no roles in a fresh project.
  grant "$BUILD_SA" roles/run.builder
  echo "    ok"

  if [ -n "$ROUTES_KEY" ]; then
    step "Routes key -> Secret Manager ($ROUTES_SECRET)"
    if ! gcloud secrets describe "$ROUTES_SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
      printf '%s' "$ROUTES_KEY" | gcloud secrets create "$ROUTES_SECRET" --data-file=- \
        --replication-policy automatic --project "$PROJECT_ID"
    elif [ "$(gcloud secrets versions access latest --secret "$ROUTES_SECRET" --project "$PROJECT_ID")" != "$ROUTES_KEY" ]; then
      printf '%s' "$ROUTES_KEY" | gcloud secrets versions add "$ROUTES_SECRET" --data-file=- --project "$PROJECT_ID"
    else
      echo "    up to date"
    fi
    gcloud secrets add-iam-policy-binding "$ROUTES_SECRET" --member "serviceAccount:$RUN_SA" \
      --role roles/secretmanager.secretAccessor --project "$PROJECT_ID" >/dev/null
  fi

  step "Budget alert (\$50, email at 50/90/100%)"
  BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' | sed 's#^billingAccounts/##')"
  # --billing-project: the Budget API is called through this project, not gcloud's default one.
  if gcloud billing budgets list --billing-account "$BILLING_ACCOUNT" --billing-project "$PROJECT_ID" \
    --format='value(displayName)' 2>/dev/null | grep -x "$SERVICE" >/dev/null; then
    echo "    exists"
  elif ! gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" --billing-project "$PROJECT_ID" --display-name "$SERVICE" \
    --budget-amount 50USD --filter-projects "projects/$PROJECT_ID" \
    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0 >/dev/null; then
    echo "    skipped: this account cannot create budgets on $BILLING_ACCOUNT (not fatal)"
  fi
fi

# --- Build and deploy ---------------------------------------------------------------------------
step "Build (Cloud Build) and deploy"
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if ! git diff --quiet HEAD 2>/dev/null; then GIT_SHA="$GIT_SHA-dirty"; fi
# --env-vars-file replaces every env var on the service, so each deploy states the full set.
# Values are simple tokens (ids, keys, model names), so plain double-quoting is valid YAML.
{
  printf 'GCP_PROJECT_ID: "%s"\n' "$PROJECT_ID"
  printf 'GIT_SHA: "%s"\n' "$GIT_SHA"
  printf 'GOOGLE_GENAI_USE_VERTEXAI: "1"\n'
  printf 'VERTEX_LOCATION: "%s"\n' "$(env_or_dotenv VERTEX_LOCATION | grep . || echo global)"
  printf 'MAPS_JS_API_KEY: "%s"\n' "$MAPS_JS_KEY"
  printf 'MAP_ID: "%s"\n' "$(env_or_dotenv MAP_ID)"
  if [ -n "$(env_or_dotenv GEMINI_MODEL)" ]; then printf 'GEMINI_MODEL: "%s"\n' "$(env_or_dotenv GEMINI_MODEL)"; fi
  if [ -z "$ROUTES_KEY" ]; then printf 'ROUTES_CACHE_MODE: "replay"\n'; fi
} >"$ENV_FILE"

DEPLOY_ARGS=(
  "$SERVICE"
  --source "$REPO_ROOT"
  --region "$REGION"
  --project "$PROJECT_ID"
  --service-account "$RUN_SA"
  --build-service-account "projects/$PROJECT_ID/serviceAccounts/$BUILD_SA"
  --no-invoker-iam-check
  # One warm instance so the first visitor never waits on a cold start; three at most bounds cost.
  --min-instances 1
  --max-instances 3
  --concurrency 20
  # An SSE run lasts under a minute; 300 s is headroom, not an expected duration.
  --timeout 300
  --cpu 1
  --memory 1Gi
  --no-cpu-throttling
  --cpu-boost
  --env-vars-file "$ENV_FILE"
  --labels app=weathernext-route-agent
  --quiet
)
if [ -n "$ROUTES_KEY" ]; then
  DEPLOY_ARGS+=(--set-secrets "GOOGLE_MAPS_ROUTES_API_KEY=${ROUTES_SECRET}:latest")
else
  DEPLOY_ARGS+=(--clear-secrets)
fi
gcloud run deploy "${DEPLOY_ARGS[@]}"

# --- Browser key: referrer + API restrictions ---------------------------------------------------
if [ -n "$MAPS_JS_KEY" ]; then
  step "Browser Maps key: service URL + localhost, Maps JavaScript API only"
  STATUS_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')"
  REFERRERS="$URL/*,$DEV_REFERRERS"
  if [ -n "$STATUS_URL" ] && [ "$STATUS_URL" != "$URL" ]; then REFERRERS="$REFERRERS,$STATUS_URL/*"; fi
  KEY_NAME="$(gcloud services api-keys lookup "$MAPS_JS_KEY" --format='value(name)')"
  # The finished operation prints the key's metadata; show it only if the update fails.
  if ! KEY_OUT="$(gcloud services api-keys update "$KEY_NAME" --allowed-referrers="$REFERRERS" \
    --api-target=service=maps-backend.googleapis.com --quiet 2>&1)"; then
    echo "$KEY_OUT" >&2
    exit 1
  fi
  echo "    referrers: $REFERRERS"
else
  echo "warning: MAPS_JS_API_KEY is not set; the map will not load" >&2
fi

step "Smoke test"
"$REPO_ROOT/scripts/smoke.sh" "$URL" --live

cat <<EOF

==> Live: $URL
    Logs:      gcloud run services logs tail $SERVICE --region $REGION --project $PROJECT_ID
    Revisions: gcloud run revisions list --service $SERVICE --region $REGION --project $PROJECT_ID
    Rollback:  gcloud run services update-traffic $SERVICE --to-revisions <REVISION>=100 --region $REGION --project $PROJECT_ID
    After the showcase, stop the always-on instance:
               gcloud run services update $SERVICE --min-instances 0 --region $REGION --project $PROJECT_ID
EOF
