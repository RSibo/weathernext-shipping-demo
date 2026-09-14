# weathernext-route-disruption-agent

Weather-Aware Operations Copilot that fuses WeatherNext 3 forecasts with a fleet's active routes.
Gemini Flash reasons over pre-validated tool output and drafts dispatcher advisories; every number
and every hazard polygon it sees is computed deterministically in Python, so Gemini never invents
weather metrics or geometry.

> **Data access note:** live forecasts require allowlist access to Google's WeatherNext 3
> Analytics Hub listing (see `scripts/01_discover_weathernext_listing.md`). You don't need it to
> try this repo: **Simulation Mode**, enabled by default in `ui/app.py`, runs the real Gemini
> reasoning and real Google Maps routing against fabricated weather scenarios at zero cost.

See [Known Limitations & Roadmap](#known-limitations--roadmap) before assuming this covers your
use case, in particular if you're not trucking/freight, or you need continuous automated
monitoring rather than on-demand checks.

## Architecture

```
Shipment -> agent/loop.py (hand-rolled google-genai function-calling loop, Gemini Flash)
              |-- check_route_weather()   -> tools/weather.py  -> BigQuery GIS (WeatherNext 3)
              `-- compute_detour_route()  -> tools/routing.py  -> Google Maps Routes API
ui/app.py (Streamlit + PyDeck) calls agent.loop.run_advisory_with_context directly.
mcp_server.py exposes the same tools/ functions over MCP for external clients.
```

Both agent-facing tools take only a `shipment_id`; coordinates, weather metrics, and hazard
geometry are resolved from internal state, never supplied by Gemini as arguments (see
`agent/tool_schemas.py` and `agent/loop.py`).

Google Maps Routes API has no "avoid polygon" parameter. `tools/routing.py` instead requests
alternative routes and filters out any whose path (via shapely) intersects the hazard polygons
`tools/weather.py` computed.

## Quickstart

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
streamlit run ui/app.py
```

Pick a mock shipment, leave the sidebar on "Simulation Mode", and click "Run Disruption Check".
That's a full run of the real Gemini agent loop and real Maps routing with zero GCP setup.

To use live WeatherNext 3 data instead of Simulation Mode, run the numbered scripts in `scripts/`
in order:

- `00_enable_routes_api.sh`: enables `routes.googleapis.com`.
- `01_discover_weathernext_listing.md`: how to request WeatherNext 3 access and find your
  subscribed listing's identifiers.
- `02_subscribe_analytics_hub_listing.sh`: subscribes the listing into your GCP project.
- `03_create_maps_routes_api_key.sh`: creates a Routes-API-restricted Maps key.
- `04_verify_setup.sh`: read-only smoke test of all of the above; safe to re-run anytime.

## Running

Live `check_route_weather` calls cost real BigQuery money, roughly $2-6 per shipment check at
on-demand rates with the default settings (see `config.WEATHERNEXT_INIT_LOOKBACK_HOURS` to tune
it). Results are cached for an hour and every query carries a hard `maximum_bytes_billed` safety
cap. Use "Simulation Mode" in the sidebar for cost-free demo runs.

## Testing

```bash
pytest --cov --cov-report=term-missing
```

69 tests, ~81% coverage, no live credentials required. BigQuery and Routes API calls are mocked,
and the Gemini function-calling loop replays from committed, sanitized cassettes in
`tests/cassettes/` (recorded via `python -m tests._record_cassettes`, which does need live
credentials and makes real billed calls).

## MCP server

```bash
python -m mcp_server --transport stdio
```

Exposes `check_route_weather` and `compute_detour_route` as MCP tools, wrapping the same
`tools/` functions the agent loop uses.

## Known Limitations & Roadmap

Verified directly against the real dataset and codebase, so another team doesn't have to
rediscover these the hard way:

- **No hail data.** WeatherNext 3 has no hail field in either table; a different data source is
  needed if hail risk matters for your vertical.
- **No true 5km wind/precipitation resolution.** The 0.1° (~10km) table has wind and
  precipitation (what this repo uses); the 0.05° (~5km) table has temperature/dewpoint only.
- **On-demand, not autonomous-scheduled.** A check runs when a dispatcher clicks "Run Disruption
  Check" or an external MCP client calls the tool; there's no polling loop, cron, or push
  mechanism monitoring routes continuously. Gemini's *reasoning* is autonomous; the *invocation*
  is not.
- **Trucking/freight only.** `tools/thresholds.py`'s safety model (dry-van rollover wind
  thresholds) is road-vehicle-specific. Maritime, rail, and retail-distribution use cases would
  need their own threshold models and likely their own `Shipment`/cargo schema.
- **MCP is a secondary interface, not the primary path.** `ui/app.py` talks to Gemini through the
  hand-rolled loop in `agent/loop.py`, not through `mcp_server.py`. Both wrap the identical
  `tools/` functions, so they can't disagree, but `mcp_server.py` is an adapter for *external* MCP
  clients, not how the demo itself reasons.
- **Mock data only.** `data/mock_shipments.json` is static fixture data. `data/__init__.py`'s
  `load_mock_shipments()` is the one function to replace with a real TMS/ERP integration.
- **The UI's "Est. Reroute Cost" is a rough estimate of the detour's own extra-mileage cost**
  (`config.DETOUR_COST_PER_MILE_USD`), not a certified figure and not a claim about cost *avoided*
  by rerouting. The avoided cost (an accident, cargo damage, delay penalties) is real but
  unquantifiable and isn't invented here.

## Configuration reference

All of these live in `config.py`, sourced from environment variables (copy `.env.example` to
`.env` and fill in).

| Variable | Default | Purpose |
|---|---|---|
| `GCP_PROJECT_ID` | none, required | Your GCP project (BigQuery + Vertex AI). |
| `BQ_LOCATION` | `US` | BigQuery dataset location. |
| `WEATHERNEXT_DATASET` | `weathernext_3` | Your subscribed Analytics Hub dataset name. |
| `WEATHERNEXT_TABLE` | `weathernext_3_0_0_0p1deg` | WeatherNext 3 forecast table/view. |
| `WEATHERNEXT_WIND_VARIABLE` | `wind_speed_10m` | Wind field name; re-verify against your own schema. |
| `WEATHERNEXT_INIT_LOOKBACK_HOURS` | `24` | How far back to search for a fresh forecast init. Wider costs much more (see `scripts/01_discover_weathernext_listing.md`). |
| `WEATHERNEXT_MAX_BYTES_BILLED` | 2 TB | Hard safety cap per BigQuery query. |
| `WEATHERNEXT_CACHE_TTL_SECONDS` | `3600` | In-memory result cache TTL per shipment check. |
| `GOOGLE_MAPS_ROUTES_API_KEY` | none, required for live routing | Routes-API-restricted Maps key. |
| `DETOUR_COST_PER_MILE_USD` | `2.00` | Blended per-mile cost used only for the UI's detour cost estimate. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model for the agent loop. |
| `VERTEX_LOCATION` | `global` | Vertex AI location for the Gemini client. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `1` | Use Vertex AI (vs. the Gemini Developer API) as the backend. |

## Adapting this for your own use case

This repo is meant to be forked, not just read.

- **Safety thresholds** (`tools/thresholds.py`): wind/precipitation/black-ice thresholds and the
  cargo-aware dry-van wind threshold are demo-calibrated, not a certified dispatch safety
  standard; replace with your own operational limits.
- **Your fleet's data** (`data/mock_shipments.json`): swap in real shipments, or replace
  `data/__init__.py`'s `load_mock_shipments()` with a loader against your own ERP/TMS.
- **The Gemini system prompt** (`agent/system_instruction.py`): if a config change alters what the
  tools return (new fields, different thresholds), re-record the cassettes
  (`python -m tests._record_cassettes`); `test_system_instruction_matches_what_cassettes_were_recorded_against`
  fails loudly if the prompt and committed cassettes drift apart.
- **Scaling beyond a single process:** the weather-result cache in `tools/weather.py` is
  deliberately process-local (a plain dict), correct for one Streamlit instance or one MCP server
  process. A multi-instance deployment (e.g. multiple Cloud Run instances) needs a shared store
  (Memorystore, Firestore, or similar) instead.
