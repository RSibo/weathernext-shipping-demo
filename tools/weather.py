"""check_route_weather: BigQuery GIS spatial-temporal join against WeatherNext 3.

PLACEHOLDER SCHEMA: the column names below (wind_speed_mph, precip_mm_hr, temp_f, init_time,
valid_time, geography) are copied from the blueprint's guesses. The real WeatherNext 3 listing has
not been subscribed to yet (see scripts/01_discover_weathernext_listing.md) — replace these with
the real schema before relying on this against live data. The QUALIFY/ROW_NUMBER
nearest-valid-time-then-nearest-distance pattern below is independent of exact column names and
should survive that correction.

Degree-based shapely buffering for hazard polygons is not metrically uniform (a fixed-degree
buffer is ~constant in latitude but shrinks in longitude distance as latitude increases). Accepted
as a deliberate simplification for a continental-US demo; a production version would reproject to
a local equal-area/UTM CRS before buffering.
"""

from __future__ import annotations

from typing import Optional

import shapely.geometry
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
  SELECT * FROM UNNEST(@waypoints)
),
candidates AS (
  SELECT
    w.name,
    w.lat,
    w.lon,
    w.eta,
    f.wind_speed_mph,
    f.precip_mm_hr,
    f.temp_f,
    f.valid_time,
    ST_DISTANCE(f.geography, ST_GEOGPOINT(w.lon, w.lat)) AS dist_m,
    ABS(TIMESTAMP_DIFF(f.valid_time, w.eta, MINUTE)) AS time_diff_min
  FROM waypoints w
  JOIN `{project}.{dataset}.{table}` f
    ON ST_DWITHIN(f.geography, ST_GEOGPOINT(w.lon, w.lat), @grid_radius_m)
   AND f.init_time BETWEEN TIMESTAMP_SUB(w.eta, INTERVAL 2 DAY) AND w.eta
   AND f.valid_time BETWEEN TIMESTAMP_SUB(w.eta, INTERVAL 3 HOUR) AND TIMESTAMP_ADD(w.eta, INTERVAL 3 HOUR)
)
SELECT name, lat, lon, eta, wind_speed_mph, precip_mm_hr, temp_f, valid_time, dist_m, time_diff_min
FROM candidates
QUALIFY ROW_NUMBER() OVER (PARTITION BY name ORDER BY time_diff_min ASC, dist_m ASC) = 1
"""

_bq_client: Optional[bigquery.Client] = None


def _get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=config.PROJECT_ID)
    return _bq_client


def _build_hazard_polygon(lat: float, lon: float, waypoint_name: str, reason_summary: str) -> HazardPolygon:
    half = config.WEATHERNEXT_GRID_DEGREES / 2
    cell = shapely.geometry.box(lon - half, lat - half, lon + half, lat + half)
    buffered = cell.buffer(config.HAZARD_BUFFER_DEGREES)
    return HazardPolygon(
        geojson=shapely.geometry.mapping(buffered),
        waypoint_name=waypoint_name,
        reason_summary=reason_summary,
    )


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
    grid_radius_m = (config.WEATHERNEXT_GRID_DEGREES / 2) * 111_000  # degrees -> meters, approximate

    query = _QUERY_TEMPLATE.format(
        project=config.PROJECT_ID,
        dataset=config.WEATHERNEXT_DATASET,
        table=config.WEATHERNEXT_TABLE,
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("waypoints", "STRUCT", waypoint_structs),
            bigquery.ScalarQueryParameter("grid_radius_m", "FLOAT64", grid_radius_m),
        ]
    )

    rows = list(client.query(query, job_config=job_config).result())

    observations: list[WeatherObservation] = []
    for row in rows:
        breached, reasons = evaluate_breach(
            wind_speed_mph=row["wind_speed_mph"],
            precip_mm_hr=row["precip_mm_hr"],
            temp_f=row["temp_f"],
        )
        hazard_polygon = None
        if breached:
            hazard_polygon = _build_hazard_polygon(
                lat=row["lat"],
                lon=row["lon"],
                waypoint_name=row["name"],
                reason_summary="; ".join(reasons),
            )
        observations.append(
            WeatherObservation(
                waypoint_name=row["name"],
                location=Coordinate(lat=row["lat"], lon=row["lon"]),
                valid_time=row["valid_time"],
                wind_speed_mph=row["wind_speed_mph"],
                precip_mm_hr=row["precip_mm_hr"],
                temp_f=row["temp_f"],
                breached=breached,
                reasons=reasons,
                hazard_polygon=hazard_polygon,
            )
        )

    return RouteWeatherResult(shipment_id=request.shipment_id, observations=observations)
