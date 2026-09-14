# WeatherNext 3 Analytics Hub subscription

**Status: LIVE and verified (2026-09-14).** Access was approved via the official WeatherNext Data
Request form, the listing was subscribed, and the real schema has been queried directly. Every
"unverified guess" this file used to track is now resolved — one confirmed our assumption, one
contradicted it (and had been silently defeating a safety threshold), and one is a genuinely new
finding about the dataset's cost profile.

## Live resource identifiers

- Linked dataset: `weathernext3-route-agent:weathernext_3` (source:
  `866962084172:WeatherNext_3`, via Analytics Hub exchange `weathernext_19397e1bcb7`, listing
  `weathernext_3_1a067c1e929`, region `us`).
- Table used: `weathernext_3_0_0_0p1deg` (a VIEW, not a base table — `bq ls` shows `Type: VIEW`;
  its `INFORMATION_SCHEMA.VIEWS` definition is hidden from subscribers, so its internals are
  opaque and must be treated empirically).

## Verified findings (superseding all earlier guesses)

1. **CONFIRMED — wind**: `wind_speed_10m_{stat}` exists as a direct field (no u/v component
   decomposition needed) and is in **m/s**. Real sample values ~7.2-7.6 (physically reasonable as
   m/s; absurdly calm as mph). `config.WEATHERNEXT_WIND_VARIABLE`'s existing default was correct.
2. **CONTRADICTED OUR ASSUMPTION — precipitation is in METERS, not already mm.** Real sample
   values for `total_precipitation_1hr_mean`/`imerg_tp_1hr_mean` are ~3e-5 to 6e-5 — consistent
   with meters (ERA5/ECMWF convention), and would have made the 25mm/hr storm threshold
   effectively unreachable if left unconverted (a real bug that shipped before this was caught).
   `tools/weather.py` now applies `_meters_to_mm`.
3. **NEW FINDING — two precipitation fields exist**: `total_precipitation_1hr` (raw model output)
   and `imerg_tp_1hr` (same order of magnitude, ruling out "already-mm" for either). Given
   Google's docs state precipitation is "calibrated against satellite observations (IMERG)",
   `tools/weather.py` uses `imerg_tp_1hr_p90` as the calibrated field.
4. **CONFIRMED — temperature is in Kelvin** (sample values ~293.6-293.9K ≈ 68-69°F).
5. **CORRECTED — the 8.19TB query cost was a real, fixable bug, NOT an inherent dataset
   property.** An earlier version of this file (and `tools/weather.py`'s docstring) claimed the
   cost was a property of the dataset's physical layout. A second-opinion review caught that this
   was wrong and I verified it directly: the query's `init_time` lookback bound was hardcoded as
   `INTERVAL 16 DAY`, conflating *forecast horizon* (how far into the future one init reaches, up
   to 360h — irrelevant here) with *init lookback* (how far into the past to search for a fresh
   init — inits occur at minimum every 6h, so ~24h is ample). Running the *actual* production
   query live with only the interval changed: `16 DAY` = 7.45TB (~$51), `24 HOUR` = 363GB (~$2.27,
   95% less), `12 HOUR` = 61GB (~$0.38, 99% less). Now templated via
   `config.WEATHERNEXT_INIT_LOOKBACK_HOURS` (default 24h) instead of hardcoded in two places that
   had already drifted from each other's intent once. Real cost is dominated by this window's
   width, not waypoint count (a 5-waypoint query cost the same as a 2-waypoint one at the same
   window — 848GB for both at 48h). `config.WEATHERNEXT_MAX_BYTES_BILLED` (2TB) remains as a
   genuine safety net against a misconfigured/runaway query, and the in-memory TTL cache in
   `tools/weather.py` (`config.WEATHERNEXT_CACHE_TTL_SECONDS`, default 1h) still avoids re-paying
   even this much smaller real cost on repeated checks. A single real query takes on the order of
   a minute to complete — noticeable but acceptable behind the UI's loading spinner.
6. **CAUGHT ONLY BY LIVE TESTING — BigQuery rejects `PARTITION BY` on `FLOAT64` columns.** The
   waypoint-name-collision fix (partitioning by `(name, lat, lon, eta)` instead of bare `name`)
   passed every mocked test but failed immediately against the real engine:
   `"Partitioning by expressions of type FLOAT64 is not allowed"`. Fixed by casting `lat`/`lon` to
   `STRING` (rounded to 5 decimal places first, defensively) in both `QUALIFY` clauses. No mock can
   catch a real SQL engine restriction like this — this is exactly why the plan called for real
   end-to-end validation before trusting the query, not just a green mocked test suite.
7. **CONFIRMED — real inits are hourly** (e.g. 2026-09-13 13:00 through 17:00 UTC all present),
   consistent with the documented 1-hour interim cadence between 6-hour inits. The
   `waypoint_inits`/`best_init_per_waypoint` horizon-eligibility CTEs (6-hourly inits → 360h
   horizon, interim inits → 48h horizon) are this repo's own design addition beyond Google's
   single-location example queries.

## If you need to re-derive any of this

```bash
bq show --format=prettyjson weathernext3-route-agent:weathernext_3   # linked dataset metadata
bq show --schema --format=prettyjson weathernext3-route-agent:weathernext_3.weathernext_3_0_0_0p1deg
```

To inspect real sample values cheaply: avoid any spatial predicate (`ST_INTERSECTS`/`ST_DWITHIN`)
when just peeking at values — a bare `init_time` filter with no spatial predicate costs only a few
GB. Always keep the `init_time` lookback bound tight (hours, not days — see finding #5) and pass
`--maximum_bytes_billed` when running anything exploratory for real, not just `--dry_run`.
