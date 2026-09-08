from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import shapely.geometry
import shapely.wkt

import config
from schemas import Coordinate, RouteWeatherRequest, Waypoint
from tools.weather import run_weather_check

_HAZARD_CELL_WKT = "POLYGON((-97.0 32.7, -96.6 32.7, -96.6 33.1, -97.0 33.1, -97.0 32.7))"


def _make_request() -> RouteWeatherRequest:
    return RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=datetime.now(timezone.utc)),
            Waypoint(name="breach-stop", location=Coordinate(lat=32.78, lon=-96.81), eta=datetime.now(timezone.utc)),
        ],
    )


def _row(name, lat, lon, *, temp_k, precip_mm, wind_mph, geography_polygon=_HAZARD_CELL_WKT):
    return {
        "name": name,
        "lat": lat,
        "lon": lon,
        "valid_time": datetime.now(timezone.utc),
        "temperature_2m_mean": temp_k,
        "total_precipitation_1hr_p90": precip_mm,
        f"{config.WEATHERNEXT_WIND_VARIABLE}_p90": wind_mph,
        "geography_polygon": geography_polygon,
    }


def _mock_client(rows):
    client = MagicMock()
    query_job = MagicMock()
    query_job.result.return_value = rows
    client.query.return_value = query_job
    return client


def test_no_rows_breached_when_below_thresholds():
    # 294.26K ~= 70F, well below every threshold.
    rows = [_row("clear-stop", 29.76, -95.37, temp_k=294.26, precip_mm=0, wind_mph=10)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=datetime.now(timezone.utc))],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.any_breach is False
    assert result.observations[0].breached is False
    assert result.observations[0].hazard_polygon is None


def test_breach_row_gets_hazard_polygon_built_from_real_geography_polygon():
    rows = [_row("breach-stop", 32.78, -96.81, temp_k=294.26, precip_mm=0, wind_mph=52.0)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[Waypoint(name="breach-stop", location=Coordinate(lat=32.78, lon=-96.81), eta=datetime.now(timezone.utc))],
    )

    result = run_weather_check(request, bq_client=client)

    obs = result.observations[0]
    assert result.any_breach is True
    assert obs.breached is True
    assert "Wind" in obs.reasons[0]
    assert obs.hazard_polygon is not None

    # The hazard polygon must be derived from the row's real geography_polygon WKT, not a
    # synthetic box — round-trip through shapely and confirm it's the (buffered) real cell.
    shape = shapely.geometry.shape(obs.hazard_polygon.geojson)
    real_cell = shapely.wkt.loads(_HAZARD_CELL_WKT)
    assert shape.is_valid
    assert shape.contains(real_cell.centroid)


def test_kelvin_to_fahrenheit_conversion():
    # 273.15K == 32F exactly.
    rows = [_row("clear-stop", 29.76, -95.37, temp_k=273.15, precip_mm=0, wind_mph=5)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=datetime.now(timezone.utc))],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.observations[0].temp_f == 32.0


def test_query_uses_real_schema_shape_not_placeholder():
    rows = [_row("clear-stop", 29.76, -95.37, temp_k=294.26, precip_mm=0, wind_mph=10)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    assert client.query.called
    call_args = client.query.call_args
    sql_text = call_args[0][0]
    job_config = call_args[1]["job_config"]

    # Structural correction locked in: real spatial join + nested forecast array, not the old
    # placeholder's point-distance approximation.
    assert "ST_INTERSECTS" in sql_text
    assert "UNNEST(t1.forecast)" in sql_text
    assert "ST_DWITHIN" not in sql_text
    assert "geography_polygon" in sql_text

    # Waypoint data must never be string-interpolated directly into the SQL.
    assert "clear-stop" not in sql_text
    assert "-95.37" not in sql_text

    param_names = {p.name for p in job_config.query_parameters}
    assert param_names == {"waypoints"}

    waypoints_param = next(p for p in job_config.query_parameters if p.name == "waypoints")
    assert waypoints_param.array_type == "STRUCT"
    assert len(waypoints_param.values) == 2


def test_query_respects_init_horizon_eligibility_rule():
    """Locks in the per-waypoint init-eligibility CTE structure (6-hourly inits => 360h horizon,
    interim 1-hourly inits => 48h horizon) — this is this module's own design addition beyond the
    documented example queries and has no live table to validate against yet, so at minimum the
    generated SQL must contain the guard."""
    rows = [_row("clear-stop", 29.76, -95.37, temp_k=294.26, precip_mm=0, wind_mph=10)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    sql_text = client.query.call_args[0][0]
    assert "MOD(EXTRACT(HOUR FROM ci.init_time), 6) = 0" in sql_text
    assert "<= 360" in sql_text
    assert "<= 48" in sql_text
