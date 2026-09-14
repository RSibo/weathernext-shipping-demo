# WeatherNext 3 integration and schema reference

Access is allowlist-gated via Google's official WeatherNext Data Request form; there is no
published approval SLA. Once approved, subscribe the listing with
`02_subscribe_analytics_hub_listing.sh`.

## Listing identifiers

Google's public WeatherNext 3 Analytics Hub listing (US region), already the default in
`02_subscribe_analytics_hub_listing.sh`:

- Publisher project: `866962084172` (`WeatherNext_3`)
- Exchange: `weathernext_19397e1bcb7`
- Listing: `weathernext_3_1a067c1e929`

Table used: `weathernext_3_0_0_0p1deg`, a VIEW (not a base table); its
`INFORMATION_SCHEMA.VIEWS` definition is hidden from subscribers, so treat its internals as
opaque and verify empirically against your own subscription.

## Schema and units quick reference

- **Wind:** `wind_speed_10m_{stat}`, direct field, **m/s** (not mph, no u/v decomposition needed).
- **Precipitation:** `imerg_tp_1hr_{stat}` (IMERG-calibrated) and `total_precipitation_1hr_{stat}`
  (raw model output), both in **meters**, not millimeters. `tools/weather.py` uses
  `imerg_tp_1hr_p90` and converts with `_meters_to_mm`.
- **Temperature:** `temperature_2m_{stat}`, **Kelvin**.
- **Forecast cadence:** inits are hourly (interim inits between the 6-hourly full-horizon inits),
  each interim init covering a 48h horizon and each 6-hourly init covering the full 360h horizon.
  **Observed caveat (2026-09-14):** the freshest `init_time` actually available in this project's
  subscribed listing was ~7 hours old at query time (last init 21:00 UTC, checked at 04:08 UTC),
  not within the last hour as the advertised cadence implies. Likely Analytics-Hub-linked-dataset
  replication lag rather than a property of WeatherNext 3's own pipeline, but not verified either
  way; `run_weather_check`'s results carry `forecast_issued_at` so callers can check actual
  freshness themselves rather than assume it.

## Query guardrails

- **Keep the `init_time` lookback bound at 24-48 hours, not days.** BigQuery's partition pruning
  keys off this bound directly; widening it scans disproportionately more data regardless of how
  selective a downstream join is. At the production query's real shape: 16 days = 7.45TB (~$51),
  24 hours = 363GB (~$2.27, 95% less), 12 hours = 61GB (~$0.38, 99% less). Set via
  `config.WEATHERNEXT_INIT_LOOKBACK_HOURS`. Cost is dominated by this window's width, not waypoint
  count.
- **`PARTITION BY`/`QUALIFY` on `lat`/`lon` requires casting to `STRING`.** BigQuery rejects
  `PARTITION BY` on `FLOAT64` columns directly; round to 5 decimal places first, then cast.
- **Avoid spatial predicates (`ST_INTERSECTS`/`ST_DWITHIN`) when just inspecting sample values.**
  A bare `init_time` filter with no spatial predicate costs only a few GB. Always pass
  `--maximum_bytes_billed` when running anything exploratory for real, not just `--dry_run`.

## Useful CLI commands

```bash
bq show --format=prettyjson "${GCP_PROJECT_ID}:${WEATHERNEXT_DATASET}"
bq show --schema --format=prettyjson "${GCP_PROJECT_ID}:${WEATHERNEXT_DATASET}.${WEATHERNEXT_TABLE}"
```
