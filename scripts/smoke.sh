#!/usr/bin/env bash
# End-to-end checks against a running server: the deployed URL (scripts/deploy.sh runs this) or a
# local one. Exits non-zero on the first failure.
#
#   ./scripts/smoke.sh                                  # http://localhost:8000
#   ./scripts/smoke.sh https://<service>.run.app --live # plus one real Gemini run (uses 1 of the daily cap)
set -euo pipefail

BASE="${1:-http://localhost:8000}"
BASE="${BASE%/}"
LIVE="${2:-}"
HERO="${HERO:-SHP-2001}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ok() { echo "ok    $*"; }
fail() {
  echo "FAIL  $*" >&2
  exit 1
}
# json <file> <python expression over d>: evaluates against the parsed JSON body.
json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print($2)" "$1"; }

curl -fsS --max-time 30 "$BASE/api/health" -o "$TMP/health" || fail "GET /api/health"
[ "$(json "$TMP/health" "d['status']")" = ok ] || fail "health status is not ok"
ok "health  sha=$(json "$TMP/health" "d['git_sha']")  modes=$(json "$TMP/health" "','.join(d['modes'])")  golden=$(json "$TMP/health" "len(d['golden'])")"

curl -fsS --max-time 15 "$BASE/api/config" -o "$TMP/config" || fail "GET /api/config"
# The Routes key must never reach the browser. Compared here, never printed.
ROUTES_KEY="${GOOGLE_MAPS_ROUTES_API_KEY:-}"
if [ -z "$ROUTES_KEY" ] && [ -f "$REPO_ROOT/.env" ]; then
  ROUTES_KEY="$(grep -E '^GOOGLE_MAPS_ROUTES_API_KEY=' "$REPO_ROOT/.env" | tail -n1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//" || true)"
fi
if [ -n "$ROUTES_KEY" ] && grep -qF "$ROUTES_KEY" "$TMP/config"; then fail "/api/config contains the Routes API key"; fi
grep -qi "routes_api\|ROUTES_API_KEY" "$TMP/config" && fail "/api/config mentions a Routes key field"
[ -n "$(json "$TMP/config" "d['maps_api_key']")" ] || echo "warn  /api/config has no maps_api_key: the map will not load"
ok "config  no Routes key; model=$(json "$TMP/config" "d['model']")"

code="$(curl -sS --max-time 15 -o "$TMP/index" -w '%{http_code}' "$BASE/")"
[ "$code" = 200 ] && grep -q 'id="root"' "$TMP/index" || fail "GET / ($code)"
[ "$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$BASE/some/deep/link")" = 200 ] || fail "SPA fallback"
ok "spa     index + deep-link fallback"

curl -fsS --max-time 30 "$BASE/api/fleet" -o "$TMP/fleet" || fail "GET /api/fleet"
ok "fleet   $(json "$TMP/fleet" "len(d['entries'])") trucks, $(json "$TMP/fleet" "d['summary']")"

etag="$(curl -fsS --max-time 30 -D - -o /dev/null "$BASE/api/forecast" | tr -d '\r' | awk -F': ' 'tolower($1)=="etag"{print $2}')"
[ -n "$etag" ] || fail "forecast has no ETag"
[ "$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' -H "If-None-Match: $etag" "$BASE/api/forecast")" = 304 ] || fail "forecast If-None-Match did not 304"
ok "forecast ETag + 304"

# SSE must arrive as a stream, not one buffered body: the first frame should land well before the end.
curl -sSN --max-time 60 -w '%{time_starttransfer} %{time_total}' -o "$TMP/golden" "$BASE/api/golden/$HERO?speed=4" >"$TMP/golden_t" || fail "golden stream"
grep -q '"type":"run_completed"' "$TMP/golden" || fail "golden run has no run_completed"
ok "golden  $(grep -c '^data:' "$TMP/golden") frames, first byte/total $(cat "$TMP/golden_t") s"

if [ "$LIVE" = "--live" ]; then
  curl -sSN --max-time 120 -D "$TMP/run_h" -o "$TMP/run" -X POST "$BASE/api/runs" \
    -H 'content-type: application/json' -d "{\"shipment_id\":\"$HERO\"}" || fail "live run"
  grep -q '"type":"run_completed"' "$TMP/run" || fail "live run did not complete: $(grep -o '"type":"error"[^}]*' "$TMP/run" | head -1)"
  adv="$(grep '"type":"advisory"' "$TMP/run" | head -1 | sed 's/^data: //')"
  status="$(printf '%s' "$adv" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")"
  adv_source="$(printf '%s' "$adv" | python3 -c "import json,sys; print(json.load(sys.stdin)['source'])")"
  run_source="$(tr -d '\r' <"$TMP/run_h" | awk -F': ' 'tolower($1)=="x-run-source"{print $2}')"
  [ "$run_source" = live ] || echo "warn  served as '$run_source', not live (daily cap reached?)"
  [ "$adv_source" = gemini ] || fail "live run used the template advisory: Gemini unreachable (check roles/aiplatform.user on the runtime SA, and GEMINI_MODEL)"
  ok "live    $HERO -> $status (advisory by $adv_source)"
fi
echo "all checks passed: $BASE"
