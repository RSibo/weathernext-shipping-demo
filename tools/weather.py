"""check_route_weather: BigQuery GIS spatial-temporal join against WeatherNext 3.

Query structure matches Google's official WeatherNext 3 BigQuery documentation (real table names,
real column-naming convention, real example queries), verified live against the subscribed dataset
`weathernext3-route-agent.weathernext_3` on 2026-09-14:

1. Wind: `wind_speed_10m_{stat}` exists as a direct field (no u/v component decomposition needed)
   and is in m/s — confirmed from real sample values (~7-8, physically reasonable as m/s, absurdly
   calm as mph) — converted via `_ms_to_mph` below.
2. Precipitation: CONTRARY to the original assumption, `total_precipitation_1hr_*` and
   `imerg_tp_1hr_*` are both in METERS, not already mm — confirmed from real sample values
   (~3e-5 to 6e-5, consistent with meters; as already-mm this would make the storm threshold,
   25mm/hr, effectively unreachable). Converted via `_meters_to_mm` below. Uses `imerg_tp_1hr`, not
   `total_precipitation_1hr`: both fields are the same order of magnitude (ruling out one being a
   pre-calibrated mm/hr version of the other), and Google's own docs state precipitation is
   "calibrated against satellite observations (IMERG)" — `imerg_tp_1hr` is the calibrated field,
   `total_precipitation_1hr` the raw uncalibrated model output.
3. Temperature: `temperature_2m_mean` confirmed in Kelvin (real sample values ~293.6-293.9K, a
   physically reasonable ~68-69°F).

CORRECTED FINDING (2026-09-14): an earlier version of this docstring claimed the query's ~8.19TB
real cost was "a property of the dataset's physical layout, not a flaw in this query." That was
wrong, and a second-opinion review caught it. The actual cause: the query used a static
`init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 16 DAY)` bound, conflating *forecast
horizon* (how far into the future one init reaches, up to 360h) with *init lookback* (how far into
the past we need to search to find a fresh init — inits occur at minimum every 6h, so this only
ever needed to be ~24h, not 16 days). `t1`'s join also carries `t1.init_time = b.init_time` (an
exact value derived through a multi-level CTE), which I'd assumed made the wider static bound
redundant/harmless. Empirically it is not: BigQuery's pruning appears to key off the directly
visible static bound, not the joined equality, so the literal interval width directly controls
scan size regardless of the join's own selectivity. Verified live, same production query, interval
only changed: `16 DAY` = 7.45TB, `24 HOUR` = 363GB (95% less), `12 HOUR` = 61GB (99% less). Now
templated via `config.WEATHERNEXT_INIT_LOOKBACK_HOURS` (default 24) instead of hardcoded twice.
Real cost is dominated by this window's width, not waypoint count (a 5-waypoint query cost the
same as a 2-waypoint one at the same window). `WEATHERNEXT_MAX_BYTES_BILLED` remains a genuine
safety cap against a misconfigured/runaway query, and results are cached
(`WEATHERNEXT_CACHE_TTL_SECONDS`) so repeated checks of the same shipment within an hour (the
forecast update cadence) don't re-pay even the now-much-smaller real cost.

The per-waypoint init-eligibility CTE chain (6-hourly inits cover a 360h horizon; interim 1-hourly
inits only cover 48h) is this module's own design addition on top of the documented example
queries (which are single-location, single-init lookups). Real inits are confirmed hourly.

CAUGHT ONLY BY LIVE TESTING: BigQuery rejects `PARTITION BY` on `FLOAT64` columns outright
("Partitioning by expressions of type FLOAT64 is not allowed") — both `QUALIFY` clauses below cast
`lat`/`lon` to STRING for exactly this reason. No mocked test can catch a real SQL engine
restriction like this.

Degree-based shapely buffering for the hazard polygon's safety margin is not metrically uniform (a
fixed-degree buffer is ~constant in latitude but shrinks in longitude distance as latitude
increases). Accepted as a deliberate simplification for a continental-US demo; a production version
would reproject to a local equal-area/UTM CRS before buffering. The base shape itself (before
buffering) is the table's real geography_polygon, not an approximation.

Known scope limitation, not a bug: weather is only checked at named waypoints (origin,
intermediate stops, destination), never sampled along the road between them — this traces back to
the original blueprint's own interface (fixed waypoints + transit hours). A storm cell sitting
squarely between two clear waypoints (e.g. mid-corridor on a 500-mile leg) would not be detected.
Properly fixing this means computing the route polyline *before* the hazard check (today Routes
API is only ever called *after* a breach, inside compute_detour_route) and sampling it every
50-75km with interpolated ETAs — a real architecture change, deliberately out of scope here rather
than bolted on ad hoc.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

import shapely.geometry
import shapely.wkt
from google.cloud import bigquery

import config
from schemas import (
    Coordinate,
    HazardPolygon,
    RouteWeatherRequest,
    RouteWeatherResult,
    WeatherObservation,
)
from tools.thresholds import evaluate_breach

_QUERY_TEMPLATE = """
WITH waypoints AS (
  SELECT * FROM UNNEST(@waypoints)  -- name STRING, lat FLOAT64, lon FLOAT64, eta TIMESTAMP
),
candidate_inits AS (
  -- Lookback window sized to reliably catch at least one fresh init (inits occur at minimum every
  -- 6h), NOT to cover the forecast horizon (a single init already reaches up to 360h on its own —
  -- irrelevant to how far back we need to search). Filtering on init_time here (and again below)
  -- is what lets this prune the init_time partition instead of scanning the whole table.
  SELECT DISTINCT init_time
  FROM `{project}.{dataset}.{table}`
  WHERE init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {lookback_hours} HOUR)
),
waypoint_inits AS (
  -- Keep only (waypoint, init) pairs where this init's horizon actually reaches the waypoint's
  -- ETA: 6-hourly inits (00/06/12/18 UTC) cover 360h; interim 1-hourly inits cover only 48h.
  -- A naive "just use MAX(init_time)" would silently return a stale/wrong match for any waypoint
  -- whose ETA is >48h out when the freshest init happens to be an interim one.
  SELECT w.name, w.lat, w.lon, w.eta, ci.init_time
  FROM waypoints w
  JOIN candidate_inits ci
    ON ci.init_time <= w.eta
   AND (
        (MOD(EXTRACT(HOUR FROM ci.init_time), 6) = 0 AND TIMESTAMP_DIFF(w.eta, ci.init_time, HOUR) <= 360)
     OR (MOD(EXTRACT(HOUR FROM ci.init_time), 6) != 0 AND TIMESTAMP_DIFF(w.eta, ci.init_time, HOUR) <= 48)
      )
),
best_init_per_waypoint AS (
  -- The freshest ELIGIBLE init per waypoint, not just the freshest init overall.
  -- Partitioned by (name, lat, lon, eta), not bare name: waypoint names are a business label,
  -- not a unique identifier — logistics routes routinely reuse generic names ("Rest Stop") or
  -- revisit the same hub twice on a round trip. Partitioning by name alone would silently
  -- collapse two distinct waypoints that happen to share a label into a single result.
  SELECT name, lat, lon, eta, init_time
  FROM waypoint_inits
  -- CAST(... AS STRING): BigQuery rejects PARTITION BY on FLOAT64 columns outright ("Partitioning
  -- by expressions of type FLOAT64 is not allowed") — confirmed live, not caught by any mocked
  -- test since it's a real BigQuery engine restriction, not something a mock can validate.
  -- ROUND(..., 5) first: harmless today (lat/lon flow from one canonical parameter value per
  -- query, never independently recomputed), but removes any future risk of two textually-different
  -- float representations of "the same" coordinate splitting into separate partitions.
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY name, CAST(ROUND(lat, 5) AS STRING), CAST(ROUND(lon, 5) AS STRING), eta
    ORDER BY init_time DESC
  ) = 1
)
SELECT
  b.name,
  b.lat,
  b.lon,
  b.eta AS waypoint_eta,
  forecast.time AS valid_time,
  forecast.temperature_2m_mean,
  forecast.imerg_tp_1hr_p90,
  forecast.{wind_var}_p90,
  t1.geography_polygon
FROM best_init_per_waypoint b
JOIN `{project}.{dataset}.{table}` t1
  -- This static bound is what actually controls scan size (see module docstring) — BigQuery's
  -- pruning keys off this directly-visible literal, not the `t1.init_time = b.init_time` equality
  -- below, even though that equality is exact. Keep this in sync with candidate_inits' bound
  -- above (both templated from the same config value, not independently hardcoded).
  ON t1.init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {lookback_hours} HOUR)
 AND t1.init_time = b.init_time
 AND ST_INTERSECTS(t1.geography_polygon, ST_GEOGPOINT(b.lon, b.lat))
CROSS JOIN UNNEST(t1.forecast) AS forecast
-- No secondary tie-break needed: t1.init_time is already pinned to exactly one value per
-- waypoint by the `t1.init_time = b.init_time` join above, so it can never disambiguate anything.
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY b.name, CAST(ROUND(b.lat, 5) AS STRING), CAST(ROUND(b.lon, 5) AS STRING), b.eta
  ORDER BY ABS(TIMESTAMP_DIFF(forecast.time, b.eta, MINUTE)) ASC
) = 1
"""

_bq_client: Optional[bigquery.Client] = None

# (shipment_id, (waypoint keys...), cargo_description) -> (cached_at_unix_time, RouteWeatherResult)
#
# Process-local only, by design: this is a single-process demo (Streamlit, or one MCP server
# instance), not a multi-instance production deployment. A scaled-out deployment (multiple Cloud
# Run instances, multiple Gunicorn/Uvicorn workers) would need a shared store instead — e.g.
# Memorystore, Firestore, or a database-backed cache — since each process would otherwise keep an
# independent, inconsistent cache. This module-level dict does NOT solve that; swap it out
# entirely rather than trying to extend it if you deploy this at that scale.
_weather_cache: dict[tuple, tuple[float, RouteWeatherResult]] = {}

# Bounds memory growth in a long-running process (the MCP server, or a Streamlit instance left
# open for days) — a bare TTL alone doesn't evict anything until the entry is looked up again, so
# a process that keeps getting *new* distinct requests without ever repeating one would otherwise
# grow the dict forever. Hand-rolled FIFO eviction (not `cachetools.TTLCache`) to avoid adding a
# dependency for what a few lines already cover correctly for this repo's scale.
_WEATHER_CACHE_MAX_SIZE = 128

# Cache-key granularity for `eta`, not query precision — the query itself still uses the exact
# `eta` for its nearest-forecast-step match. Rounding only the cache key to the forecast's native
# hourly granularity means two requests for "the same" waypoint/time a few minutes apart (e.g. a
# fresh Shipment built by a different caller, rather than the same cached object the UI reuses
# within one session) still hit the cache instead of guaranteed-missing on sub-second differences.
_CACHE_ETA_ROUNDING_MINUTES = 15


def _get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=config.PROJECT_ID)
    return _bq_client


def _round_eta_for_cache(eta):
    minutes = _CACHE_ETA_ROUNDING_MINUTES
    discard = timedelta(
        minutes=eta.minute % minutes, seconds=eta.second, microseconds=eta.microsecond
    )
    rounded = eta - discard
    if discard >= timedelta(minutes=minutes / 2):
        rounded += timedelta(minutes=minutes)
    return rounded


def _cache_key(request: RouteWeatherRequest) -> tuple:
    waypoint_keys = tuple(
        (wp.name, wp.location.lat, wp.location.lon, _round_eta_for_cache(wp.eta))
        for wp in request.waypoints
    )
    return (request.shipment_id, waypoint_keys, request.cargo_description)


def _clear_weather_cache() -> None:
    """Test hook — production code never needs to call this."""
    _weather_cache.clear()


def _build_hazard_polygon(
    geography_polygon_wkt: str, waypoint_name: str, reason_summary: str
) -> HazardPolygon:
    cell = shapely.wkt.loads(geography_polygon_wkt)
    buffered = cell.buffer(config.HAZARD_BUFFER_DEGREES)
    return HazardPolygon(
        geojson=shapely.geometry.mapping(buffered),
        waypoint_name=waypoint_name,
        reason_summary=reason_summary,
    )


def _kelvin_to_fahrenheit(kelvin: float) -> float:
    return (kelvin - 273.15) * 9 / 5 + 32


def _ms_to_mph(meters_per_second: float) -> float:
    return meters_per_second * 2.23694


def _meters_to_mm(meters: float) -> float:
    return meters * 1000


def run_weather_check(
    request: RouteWeatherRequest, *, bq_client: Optional[bigquery.Client] = None
) -> RouteWeatherResult:
    """The implementation behind the `check_route_weather` tool.

    Runs one parameterized BigQuery GIS query for all waypoints in the request, then computes
    threshold breaches and hazard polygons in Python via tools.thresholds — never in SQL and never
    left to the caller (Gemini) to infer from raw numbers. Results are cached for
    config.WEATHERNEXT_CACHE_TTL_SECONDS (default 1h, matching the forecast update cadence) since
    each real query costs real money (see module docstring) — a cache hit costs nothing.
    """
    cache_key = _cache_key(request)
    cached = _weather_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_result = cached
        if time.time() - cached_at < config.WEATHERNEXT_CACHE_TTL_SECONDS:
            return cached_result

    client = bq_client if bq_client is not None else _get_client()

    waypoint_structs = [
        bigquery.StructQueryParameter(
            None,
            bigquery.ScalarQueryParameter("name", "STRING", wp.name),
            bigquery.ScalarQueryParameter("lat", "FLOAT64", wp.location.lat),
            bigquery.ScalarQueryParameter("lon", "FLOAT64", wp.location.lon),
            bigquery.ScalarQueryParameter("eta", "TIMESTAMP", wp.eta),
        )
        for wp in request.waypoints
    ]

    query = _QUERY_TEMPLATE.format(
        project=config.PROJECT_ID,
        dataset=config.WEATHERNEXT_DATASET,
        table=config.WEATHERNEXT_TABLE,
        wind_var=config.WEATHERNEXT_WIND_VARIABLE,
        lookback_hours=config.WEATHERNEXT_INIT_LOOKBACK_HOURS,
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("waypoints", "STRUCT", waypoint_structs),
        ],
        maximum_bytes_billed=config.WEATHERNEXT_MAX_BYTES_BILLED,
    )

    rows = list(client.query(query, job_config=job_config).result())
    if not rows and request.waypoints:
        raise RuntimeError(
            f"No WeatherNext forecast records found for shipment '{request.shipment_id}'. "
            "Ensure waypoints are within the US coverage area and ETA falls within the 360-hour forecast horizon."
        )

    observations: list[WeatherObservation] = []
    # Keyed by (name, lat, lon, eta) — the same compound key the SQL above partitions by — not
    # bare name, for the same reason: two waypoints can legitimately share a name. Tracking by
    # name alone would let one waypoint's successful match mask another same-named waypoint's
    # missing data, silently reporting it as monitored when it wasn't.
    found_keys = set()
    for row in rows:
        found_keys.add((row["name"], row["lat"], row["lon"], row["waypoint_eta"]))
        temp_f = _kelvin_to_fahrenheit(row["temperature_2m_mean"])
        # max(0.0, ...): percentile/regression-fit satellite-calibrated data can occasionally emit
        # tiny negative artifacts near zero (e.g. -1e-7) — precipitation can never be negative.
        precip_mm_hr = max(0.0, _meters_to_mm(row["imerg_tp_1hr_p90"]))
        wind_speed_key = f"{config.WEATHERNEXT_WIND_VARIABLE}_p90"
        wind_speed_mph = _ms_to_mph(row[wind_speed_key])

        breached, reasons = evaluate_breach(
            wind_speed_mph=wind_speed_mph,
            precip_mm_hr=precip_mm_hr,
            temp_f=temp_f,
            cargo_description=request.cargo_description,
        )
        hazard_polygon = None
        if breached:
            hazard_polygon = _build_hazard_polygon(
                geography_polygon_wkt=row["geography_polygon"],
                waypoint_name=row["name"],
                reason_summary="; ".join(reasons),
            )
        observations.append(
            WeatherObservation(
                waypoint_name=row["name"],
                location=Coordinate(lat=row["lat"], lon=row["lon"]),
                valid_time=row["valid_time"],
                wind_speed_mph=wind_speed_mph,
                precip_mm_hr=precip_mm_hr,
                temp_f=temp_f,
                breached=breached,
                reasons=reasons,
                hazard_polygon=hazard_polygon,
            )
        )

    unmonitored = [
        wp.name
        for wp in request.waypoints
        if (wp.name, wp.location.lat, wp.location.lon, wp.eta) not in found_keys
    ]

    result = RouteWeatherResult(
        shipment_id=request.shipment_id,
        observations=observations,
        unmonitored_waypoints=unmonitored if unmonitored else None,
    )
    if cache_key not in _weather_cache and len(_weather_cache) >= _WEATHER_CACHE_MAX_SIZE:
        # FIFO, not LRU: plain dicts preserve insertion order (Python 3.7+), so the first key is
        # the oldest. Simple and sufficient for this repo's scale — see _WEATHER_CACHE_MAX_SIZE.
        oldest_key = next(iter(_weather_cache))
        del _weather_cache[oldest_key]
    _weather_cache[cache_key] = (time.time(), result)
    return result
