# weathernext-route-disruption-agent

Weather-Aware Autonomous Operations Agent that fuses WeatherNext 3 forecasts with a fleet's active
routes. Gemini Flash reasons over pre-validated tool output and drafts dispatcher advisories; every
number and every hazard polygon it sees is computed deterministically in Python — Gemini never
invents weather metrics or geometry.

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
   - `01_discover_weathernext_listing.md` — **investigated, not found.** WeatherNext 3 does not
     currently appear to exist as a public, subscribable BigQuery Analytics Hub listing — checked
     via the API (no cross-org search endpoint exists), via a live human search of BigQuery
     Studio's "+ ADD DATA" panel (only paid third-party weather connectors show up), and by
     scanning every BigQuery project reachable from this account. See that file for the full
     investigation and what to do if you get access some other way. `tools/weather.py` and
     `config.py` deliberately run against placeholder dataset/table/column names copied from the
     original blueprint until this changes.
   - `02_subscribe_analytics_hub_listing.sh` — ready to run once real listing IDs are known
     (`PUBLISHER_PROJECT`/`EXCHANGE_ID`/`LISTING_ID` env vars); not runnable today.
   - `03_create_maps_routes_api_key.sh` — done; key is in local `.env`.
   - `04_verify_setup.sh` — read-only smoke test of all of the above; safe to re-run anytime.

## Running

```bash
streamlit run ui/app.py
```

Select a mock shipment (`data/mock_shipments.json`, ETAs resolved to "now + N hours" at load time)
and click "Run Disruption Check". Until WeatherNext 3 is actually subscribed (see above —
currently not possible, not just not yet done), `check_route_weather` will fail with a BigQuery
"dataset not found" error — this is expected and surfaces as an error in the UI rather than a
silent wrong answer. Everything downstream of that call (thresholds, hazard polygons, routing,
the agent loop, the UI) is built and tested against the placeholder schema and is ready to run
the moment a real dataset/table is wired into `.env`.

## Testing

```bash
pytest --cov --cov-report=term-missing
```

32 tests, ~91% coverage over `tools/` + `agent/`, no live credentials required — BigQuery and
Routes API calls are mocked, and the Gemini function-calling loop replays from committed, sanitized
cassettes in `tests/cassettes/` (recorded via `python -m tests._record_cassettes`, which does need
live credentials and makes real billed calls).

## MCP server

```bash
python -m mcp_server --transport stdio
```

Exposes `check_route_weather` and `compute_detour_route` as MCP tools, wrapping the exact same
`tools/` functions the agent loop uses.
