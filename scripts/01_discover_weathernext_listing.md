# WeatherNext 3 Analytics Hub subscription (manual step)

**Status: access requested (2026-09-08), not yet granted.** The user submitted the official
WeatherNext Data Request form and shared Google's WeatherNext 3 BigQuery documentation, which
confirms the real table schema (below) — so `tools/weather.py` is already written against the
*real* query shape, not a guess. Only three things remain genuinely blocked on access:

1. The real **linked dataset ID** (chosen at subscribe time — can't be known in advance).
2. The exact **wind variable name** (`config.WEATHERNEXT_WIND_VARIABLE`, currently a guess:
   `wind_speed_10m`) — not shown in either of Google's example queries.
3. Whether **precipitation is in mm or meters** (currently assumed mm, per `tools/weather.py`'s
   module docstring) — needs a real row to confirm.

## Earlier investigation (superseded)

Before access was requested, this file documented an investigation that found no discoverable
public Analytics Hub listing (no cross-org search API, blocked web search, a live Console search
that only surfaced third-party connectors like AccuWeather). That conclusion is now understood
differently: WeatherNext 3 is **allowlist-gated** (the Data Request form is the actual access
path), not simply undiscoverable — which is exactly why it never showed up in a marketplace search.

## Confirmed real schema (from Google's official WeatherNext 3 BigQuery docs, 2026-09-08)

- Distributed via BigQuery Analytics Hub. Subscribing links two tables into your project's dataset:
  - `weathernext_3_0_0_0p1deg` — 0.1° grid, 19 surface variables × 6 stats (`mean`/`p10`/`p25`/
    `p50`/`p75`/`p90`) = 114 metrics, hourly timesteps. **This is the table `tools/weather.py`
    queries.**
  - `weathernext_3_0_0_0p05deg` — 0.05° grid, 2m temperature/dewpoint only (12 metrics). Not used.
- Each row is one `(geography_polygon, init_time)` cell/init pair. `geography_polygon` is the grid
  cell boundary (a GEOGRAPHY polygon, not a point) — spatial matching is
  `ST_INTERSECTS(geography_polygon, ST_GEOGPOINT(lon, lat))`, an exact point-in-cell test.
- Forecasts are a **repeated field `forecast`**, accessed via `, UNNEST(forecast) AS forecast`.
  Each element has `forecast.time` (absolute valid timestamp), `forecast.hours` (lead time from
  `init_time`), and `forecast.<variable>_<stat>` columns, e.g. `temperature_2m_mean`,
  `total_precipitation_1hr_p90`.
- `init_time` is the partition column (as originally assumed) — filter on it for partition pruning.
- Forecast horizon depends on init granularity: 6-hourly inits (00/06/12/18 UTC) → 360h horizon;
  interim 1-hourly inits (all other hours) → 48h horizon only. `tools/weather.py`'s
  `waypoint_inits`/`best_init_per_waypoint` CTEs pick the freshest *eligible* init per waypoint
  accordingly — this logic is this repo's own addition (the docs' example queries are
  single-location, single-init) and has never run against a real table. **First thing to do once
  access arrives: a manual live query to validate it**, before trusting it in the agent loop.

## Once access is granted

1. Subscribe via the Console (or `scripts/02_subscribe_analytics_hub_listing.sh` once you have the
   publisher project/exchange/listing IDs from the granted listing).
2. `bq ls --project_id=weathernext3-route-agent` to get the real linked dataset name.
3. `bq show --schema --format=prettyjson weathernext3-route-agent:<LINKED_DATASET>.weathernext_3_0_0_0p1deg`
   to confirm the real wind variable name and inspect a few real rows for precipitation units.
4. Update `.env`: `WEATHERNEXT_DATASET` (real name from step 2), and `WEATHERNEXT_WIND_VARIABLE`
   if it differs from `wind_speed_10m`.
5. If precipitation turns out to be in meters, not mm: fix the one flagged line in
   `tools/weather.py`'s `run_weather_check` (multiply `total_precipitation_1hr_p90` by 1000).
6. Run a manual live query exercising the `best_init_per_waypoint` CTE before trusting it — see
   the horizon-eligibility note above.
7. Re-run `scripts/04_verify_setup.sh` and the agent loop end-to-end (both a clear and a breached
   mock shipment) before considering this done.
