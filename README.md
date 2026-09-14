# weathernext-route-disruption-agent

Weather-Aware Operations Copilot that fuses WeatherNext 3 forecasts with a fleet's active routes.
Gemini Flash reasons over pre-validated tool output and drafts dispatcher advisories; every number
and every hazard polygon it sees is computed deterministically in Python — Gemini never invents
weather metrics or geometry.

> ⚠️ **Before you start: WeatherNext 3 access is not self-serve.** It's allowlist-gated via
> Google's official WeatherNext Data Request form, with no published approval SLA — and even once
> approved, the real Analytics Hub listing isn't reliably discoverable via the API or a Console
> search; expect to browse for it manually. This is the single biggest blocker to standing this up
> in a new project, and it has nothing to do with this codebase. Submit that request *first*,
> before investing time in anything else here. See `scripts/01_discover_weathernext_listing.md`
> for the full process. Until you have access, `ui/app.py`'s "Simulation Mode" lets you exercise
> the full Gemini + Routes API pipeline against fabricated weather data at zero cost.

See [Known Limitations & Roadmap](#known-limitations--roadmap) before assuming this covers your
use case — in particular, if you're not trucking/freight, or you need continuous automated
monitoring rather than on-demand checks.

## Architecture

```
Shipment -> agent/loop.py (hand-rolled google-genai function-calling loop, Gemini Flash)
              |-- check_route_weather()   -> tools/weather.py  -> BigQuery GIS (WeatherNext 3)
              `-- compute_detour_route()  -> tools/routing.py  -> Google Maps Routes API
ui/app.py (Streamlit + PyDeck) calls agent.loop.run_advisory_with_context directly.
mcp_server.py exposes the same tools/ functions over MCP for external clients.
```

Both agent-facing tools take only a `shipment_id` — coordinates, weather metrics, and hazard
geometry are resolved from internal state, never supplied by Gemini as arguments. See
`agent/tool_schemas.py` and `agent/loop.py` for why.

**Technical correction to the original blueprint:** the Routes API has no "avoid polygon"
parameter. `tools/routing.py` instead requests alternative routes and filters out any whose path
(via shapely) intersects the hazard polygons `tools/weather.py` computed.

## Setup

1. `uv venv --python 3.11 .venv && source .venv/bin/activate`
2. `uv pip install -e ".[dev]"`
3. `cp .env.example .env` and fill in values as the scripts below produce them.
4. Run the numbered scripts in `scripts/` in order:
   - `00_enable_routes_api.sh` — enables `routes.googleapis.com` (done, idempotent to re-run).
   - `01_discover_weathernext_listing.md` — **live and verified.** WeatherNext 3 access was
     approved (allowlist-gated via the official WeatherNext Data Request form) and the listing is
     subscribed at `weathernext3-route-agent:weathernext_3`. The real schema has been queried
     directly — see that file for the full set of verified findings, including a real
     precipitation-units correction and an important cost-profile finding (querying this dataset
     is inherently expensive; a billing cap and a result cache are both built in).
   - `02_subscribe_analytics_hub_listing.sh` — already run; kept for reference/re-subscription.
   - `03_create_maps_routes_api_key.sh` — done; key is in local `.env`.
   - `04_verify_setup.sh` — read-only smoke test of all of the above; safe to re-run anytime.

## Running

```bash
streamlit run ui/app.py
```

Select a mock shipment (`data/mock_shipments.json`, ETAs resolved to "now + N hours" at load time)
and click "Run Disruption Check". Live `check_route_weather` calls cost real BigQuery money —
roughly $2-6 per shipment check at on-demand rates (see `scripts/01_discover_weathernext_listing.md`
for the full cost story and `config.WEATHERNEXT_INIT_LOOKBACK_HOURS` if you need to tune it) — so
results are cached for an hour (`config.WEATHERNEXT_CACHE_TTL_SECONDS`) and every query carries a
hard `maximum_bytes_billed` safety cap (`config.WEATHERNEXT_MAX_BYTES_BILLED`). Use "Simulation
Mode" in the sidebar for cost-free demo runs against fabricated weather scenarios.

## Testing

```bash
pytest --cov --cov-report=term-missing
```

68 tests, ~83% coverage, no live credentials required — BigQuery and Routes API calls are mocked,
and the Gemini function-calling loop replays from committed, sanitized cassettes in
`tests/cassettes/` (recorded via `python -m tests._record_cassettes`, which does need live
credentials and makes real billed calls).

## MCP server

```bash
python -m mcp_server --transport stdio
```

Exposes `check_route_weather` and `compute_detour_route` as MCP tools, wrapping the exact same
`tools/` functions the agent loop uses.

## Known Limitations & Roadmap

Verified directly against the real dataset and codebase — not a hedge, an honest account of where
this diverges from the original "autonomous supply chain disruption agent" pitch, so another team
doesn't discover these the hard way:

- **No hail data.** WeatherNext 3 has no hail field at all — confirmed by reading the real schema
  of both tables. If hail risk matters for your vertical, this dataset can't provide it; you'd need
  a different data source entirely.
- **No true 5km wind/precipitation resolution.** The dataset's two tables are 0.1° (~10km, has
  wind/precipitation, what this repo uses) and 0.05° (~5km, but temperature/dewpoint *only* — no
  wind, no precipitation). You cannot get 5km-resolution wind or precipitation from WeatherNext 3
  as currently published.
- **On-demand, not autonomous-scheduled.** Every check happens because a human clicked "Run
  Disruption Check" or an external MCP client made a call — there is no polling loop, cron, or
  push mechanism that monitors routes continuously and raises alerts unprompted. Gemini's
  *reasoning* is autonomous (it decides whether a detour is warranted); the *invocation* is not.
  Adding real scheduled monitoring is a legitimate next step, not implemented here.
- **Trucking/freight only.** `tools/thresholds.py`'s entire safety model (dry-van rollover wind
  thresholds) is road-vehicle-specific. Maritime, rail, and retail-distribution use cases named in
  the original pitch would need their own threshold models and likely their own `Shipment`/cargo
  schema — none of that exists here.
- **MCP is a secondary interface, not the primary path.** The main demo (`ui/app.py`) talks to
  Gemini through a hand-rolled function-calling loop in `agent/loop.py`, not through
  `mcp_server.py`. Both wrap the identical underlying `tools/` functions (see that file's
  docstring), so they can't disagree — but if your integration plan assumes MCP is how the agent
  itself reasons, it isn't; it's an adapter for *external* MCP clients.
- **Mock data only.** `data/mock_shipments.json` is static fixture data, not a live feed from a
  real TMS/ERP. `data/__init__.py`'s `load_mock_shipments()` is the one function to replace with a
  real integration.
- **No dollar cost-avoidance estimate is a certified figure.** The UI's "Est. Reroute Cost" is a
  rough, clearly-labeled estimate of the detour's own extra-mileage cost (`config.DETOUR_COST_PER_MILE_USD`)
  — not a claim about cost *avoided* by rerouting. The actual avoided cost (an accident, cargo
  damage, delay penalties) is real but unquantifiable and is not invented here
  (`config.py`) — useful for a sales conversation, not a number to put in a financial model as-is.

## Adapting this for your own use case

This repo is meant to be forked, not just read. The knobs a new team actually needs to touch are
small and centralized — you shouldn't need to read every file to find them:

- **GCP project & WeatherNext access** (`config.py` / `.env`): `GCP_PROJECT_ID`,
  `WEATHERNEXT_DATASET`/`WEATHERNEXT_TABLE` (only meaningful once *you've* been granted WeatherNext
  3 access and subscribed the listing — see `scripts/01_discover_weathernext_listing.md` for the
  full process, including the Data Request form step that gates it). `WEATHERNEXT_WIND_VARIABLE`
  is confirmed correct as shipped, but re-verify it and the precipitation field/units against your
  own subscription's real schema before trusting it (`bq show --schema`) — don't assume ours holds.
- **Cost tuning** (`config.py` / `.env`): `WEATHERNEXT_INIT_LOOKBACK_HOURS` (default 24h — how far
  back to search for a fresh forecast init; wider costs more, see the cost story in
  `scripts/01_discover_weathernext_listing.md`), `WEATHERNEXT_MAX_BYTES_BILLED` (hard safety cap),
  `WEATHERNEXT_CACHE_TTL_SECONDS` (result cache TTL).
- **Safety thresholds** (`tools/thresholds.py`): wind/precipitation/black-ice thresholds and the
  cargo-aware dry-van wind threshold are explicit constants with their reasoning in comments —
  these are demo-calibrated, not a certified dispatch safety standard; replace with your own
  operational limits.
- **Your own fleet's data** (`data/mock_shipments.json`): swap in your real shipments, or replace
  `data/__init__.py`'s `load_mock_shipments()` with a loader against your own ERP/TMS.
- **The Gemini system prompt** (`agent/system_instruction.py`): if you change any of the above in a
  way that changes what the tools return (new fields, different thresholds), re-record the
  cassettes (`python -m tests._record_cassettes`) — `test_system_instruction_matches_what_cassettes_were_recorded_against`
  will fail loudly if the prompt and the committed cassettes drift apart.
- **Scaling beyond a single process**: the weather-result cache in `tools/weather.py` is
  deliberately process-local (a plain dict) — correct for one Streamlit instance or one MCP
  server process, but each instance in a multi-instance deployment (e.g. multiple Cloud Run
  instances) would keep an independent, inconsistent cache. Swap it for a shared store
  (Memorystore, Firestore, or similar) rather than extending the in-memory one if you deploy at
  that scale.
