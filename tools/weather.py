"""check_route_weather: BigQuery GIS spatial-temporal join against WeatherNext 3.

Query structure below matches Google's official WeatherNext 3 BigQuery documentation (real table
names, real column-naming convention, real example queries) — NOT the original blueprint's guessed
flat-table schema. Two things remain genuinely unverified pending actual dataset access (not
guessed past reasonable confidence):

1. The wind variable name (config.WEATHERNEXT_WIND_VARIABLE) — the docs' example queries only show
   temperature and precipitation; wind isn't named anywhere in what's been shared.
2. Precipitation units — assumed already in mm (total_precipitation_1hr_* is calibrated against
   IMERG, whose native units are mm/hr), NOT the meters-per-timestep convention common in
   ECMWF/GRIB-derived data. If real data turns out to be in meters, multiply by 1000 at the
   `precip_mm_hr = row["total_precipitation_1hr_p90"]` line in run_weather_check below — flagged
   there, not silently guessed.

The per-waypoint init-eligibility CTE chain (6-hourly inits cover a 360h horizon; interim 1-hourly
inits only cover 48h) is this module's own design addition on top of the documented example
queries (which are single-location, single-init lookups) and has never run against a real table.
First action once access arrives: a manual live query to validate it before trusting it in the
agent loop — see scripts/01_discover_weathernext_listing.md.

Degree-based shapely buffering for the hazard polygon's safety margin is not metrically uniform (a
fixed-degree buffer is ~constant in latitude but shrinks in longitude distance as latitude
increases). Accepted as a deliberate simplification for a continental-US demo; a production version
would reproject to a local equal-area/UTM CRS before buffering. The base shape itself (before
buffering) is the table's real geography_polygon, not an approximation.
"""

from __future__ import annotations

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
  -- Distinct init times within a lookback window generous enough to cover the max 360h horizon.
  -- Filtering on init_time here (and again below) is what lets this prune the init_time partition
  -- instead of scanning the whole table.
  SELECT DISTINCT init_time
  FROM `{project}.{dataset}.{table}`
  WHERE init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 16 DAY)
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
  SELECT name, lat, lon, eta, init_time
  FROM waypoint_inits
  QUALIFY ROW_NUMBER() OVER (PARTITION BY name ORDER BY init_time DESC) = 1
)
SELECT
  b.name,
  b.lat,
  b.lon,
  forecast.time AS valid_time,
  forecast.temperature_2m_mean,
  forecast.total_precipitation_1hr_p90,
  forecast.{wind_var}_p90,
  t1.geography_polygon
FROM best_init_per_waypoint b
JOIN `{project}.{dataset}.{table}` t1
  ON t1.init_time = b.init_time
 AND ST_INTERSECTS(t1.geography_polygon, ST_GEOGPOINT(b.lon, b.lat))
CROSS JOIN UNNEST(t1.forecast) AS forecast
-- No secondary tie-break needed: t1.init_time is already pinned to exactly one value per
-- waypoint by the `t1.init_time = b.init_time` join above, so it can never disambiguate anything.
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY b.name ORDER BY ABS(TIMESTAMP_DIFF(forecast.time, b.eta, MINUTE)) ASC
) = 1
"""

_bq_client: Optional[bigquery.Client] = None


def _get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=config.PROJECT_ID)
    return _bq_client


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


def run_weather_check(
    request: RouteWeatherRequest, *, bq_client: Optional[bigquery.Client] = None
) -> RouteWeatherResult:
    """The implementation behind the `check_route_weather` tool.

    Runs one parameterized BigQuery GIS query for all waypoints in the request, then computes
    threshold breaches and hazard polygons in Python via tools.thresholds — never in SQL and never
    left to the caller (Gemini) to infer from raw numbers.
    """
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
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("waypoints", "STRUCT", waypoint_structs),
        ]
    )

    rows = list(client.query(query, job_config=job_config).result())
    if not rows and request.waypoints:
        raise RuntimeError(
            f"No WeatherNext forecast records found for shipment '{request.shipment_id}'. "
            "Ensure waypoints are within the US coverage area and ETA falls within the 360-hour forecast horizon."
        )

    observations: list[WeatherObservation] = []
    found_names = set()
    for row in rows:
        found_names.add(row["name"])
        temp_f = _kelvin_to_fahrenheit(row["temperature_2m_mean"])
        # ASSUMED already mm (see module docstring) — multiply by 1000 here if real data proves to
        # be meters-per-timestep instead.
        precip_mm_hr = row["total_precipitation_1hr_p90"]
        wind_speed_key = f"{config.WEATHERNEXT_WIND_VARIABLE}_p90"
        wind_speed_mph = row[wind_speed_key]

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

    unmonitored = [wp.name for wp in request.waypoints if wp.name not in found_names]

    return RouteWeatherResult(
        shipment_id=request.shipment_id,
        observations=observations,
        unmonitored_waypoints=unmonitored if unmonitored else None,
    )
