from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import shapely.geometry

from schemas import Coordinate, RouteWeatherRequest, Waypoint
from tools.weather import run_weather_check


def _make_request() -> RouteWeatherRequest:
    return RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=datetime.now(timezone.utc)),
            Waypoint(name="breach-stop", location=Coordinate(lat=32.78, lon=-96.81), eta=datetime.now(timezone.utc)),
        ],
    )


def _row(name, lat, lon, wind, precip, temp):
    return {
        "name": name,
        "lat": lat,
        "lon": lon,
        "eta": datetime.now(timezone.utc),
        "wind_speed_mph": wind,
        "precip_mm_hr": precip,
        "temp_f": temp,
        "valid_time": datetime.now(timezone.utc),
        "dist_m": 100.0,
        "time_diff_min": 5,
    }


def _mock_client(rows):
    client = MagicMock()
    query_job = MagicMock()
    query_job.result.return_value = rows
    client.query.return_value = query_job
    return client


def test_no_rows_breached_when_below_thresholds():
    rows = [_row("clear-stop", 29.76, -95.37, wind=10, precip=0, temp=70)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=datetime.now(timezone.utc))],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.any_breach is False
    assert result.observations[0].breached is False
    assert result.observations[0].hazard_polygon is None


def test_breach_row_gets_hazard_polygon_and_reasons():
    rows = [_row("breach-stop", 32.78, -96.81, wind=52.0, precip=0, temp=70)]
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

    # GeoJSON must round-trip through shapely into a valid Polygon.
    shape = shapely.geometry.shape(obs.hazard_polygon.geojson)
    assert shape.is_valid
    assert shape.geom_type in ("Polygon", "MultiPolygon")


def test_query_uses_parameterized_struct_array_not_string_interpolation():
    rows = [_row("clear-stop", 29.76, -95.37, wind=10, precip=0, temp=70)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    assert client.query.called
    call_args = client.query.call_args
    sql_text = call_args[0][0]
    job_config = call_args[1]["job_config"]

    # The waypoint names/coords must never be interpolated directly into the SQL string.
    assert "clear-stop" not in sql_text
    assert "-95.37" not in sql_text

    param_names = {p.name for p in job_config.query_parameters}
    assert param_names == {"waypoints", "grid_radius_m"}

    waypoints_param = next(p for p in job_config.query_parameters if p.name == "waypoints")
    assert waypoints_param.array_type == "STRUCT"
    assert len(waypoints_param.values) == 2
