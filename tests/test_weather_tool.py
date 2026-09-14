from __future__ import annotations

import time as time_module
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import shapely.geometry
import shapely.wkt

import config
from schemas import Coordinate, RouteWeatherRequest, Waypoint
from tools.weather import _meters_to_mm, _ms_to_mph, run_weather_check

_HAZARD_CELL_WKT = "POLYGON((-97.0 32.7, -96.6 32.7, -96.6 33.1, -97.0 33.1, -97.0 32.7))"
# Fixed, not datetime.now(): a real wall-clock value made the eta-rounding cache test flaky —
# it failed whenever the real current minute landed near a 15-minute rounding boundary (roughly
# 1 run in 5), since _NOW and _NOW + 3min could then round to different buckets.
_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_request() -> RouteWeatherRequest:
    return RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW),
            Waypoint(name="breach-stop", location=Coordinate(lat=32.78, lon=-96.81), eta=_NOW),
        ],
    )


def _row(
    name,
    lat,
    lon,
    eta,
    *,
    temp_k,
    precip_m,
    wind_ms,
    geography_polygon=_HAZARD_CELL_WKT,
    forecast_issued_at=_NOW - timedelta(hours=1),
):
    return {
        "name": name,
        "lat": lat,
        "lon": lon,
        "waypoint_eta": eta,
        "valid_time": datetime.now(timezone.utc),
        "temperature_2m_mean": temp_k,
        "imerg_tp_1hr_p90": precip_m,
        f"{config.WEATHERNEXT_WIND_VARIABLE}_p90": wind_ms,
        "geography_polygon": geography_polygon,
        "forecast_issued_at": forecast_issued_at,
    }


def _mock_client(rows):
    client = MagicMock()
    query_job = MagicMock()
    query_job.result.return_value = rows
    client.query.return_value = query_job
    return client


def test_forecast_issued_at_flows_through_from_query_row():
    # forecast_issued_at surfaces the forecast's init_time (when WeatherNext 3 generated it) so
    # the UI can display forecast freshness -- distinct from valid_time (the forecasted moment).
    issued = _NOW - timedelta(hours=1)
    rows = [
        _row(
            "clear-stop",
            29.76,
            -95.37,
            _NOW,
            temp_k=294.26,
            precip_m=0,
            wind_ms=4.5,
            forecast_issued_at=issued,
        )
    ]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )
    result = run_weather_check(request, bq_client=client)
    assert result.observations[0].forecast_issued_at == issued

    sql_text = client.query.call_args[0][0]
    assert "b.init_time AS forecast_issued_at" in sql_text


def test_no_rows_breached_when_below_thresholds():
    # 294.26K ~= 70F, well below every threshold. 4.5 m/s ~= 10mph, well below every wind threshold.
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.any_breach is False
    assert result.observations[0].breached is False
    assert result.observations[0].hazard_polygon is None


def test_wind_speed_converted_from_ms_to_mph():
    # 20 m/s is the canonical near-rollover crosswind example (~44.7mph) — verifies the
    # conversion is actually applied, not just that a breach happens to occur.
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=20.0)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.observations[0].wind_speed_mph == _ms_to_mph(20.0)
    assert result.observations[0].wind_speed_mph == pytest.approx(44.7, abs=0.1)


def test_precip_converted_from_meters_to_mm():
    # 0.03 meters/hr == 30mm/hr — verified against real WeatherNext 3 sample values (order of
    # 1e-5 to 1e-4 meters), which ruled out the earlier "already mm" assumption: that assumption
    # would have made the 25mm/hr storm threshold effectively unreachable.
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0.03, wind_ms=0)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.observations[0].precip_mm_hr == _meters_to_mm(0.03)
    assert result.observations[0].precip_mm_hr == pytest.approx(30.0)
    assert result.observations[0].breached is True
    assert "Precipitation" in result.observations[0].reasons[0]


def test_precip_clamped_to_zero_when_source_data_is_tiny_negative():
    # Percentile/regression-fit satellite-calibrated data can occasionally emit tiny negative
    # artifacts near zero (e.g. -1e-7 meters) — precipitation can never actually be negative.
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=-1e-7, wind_ms=4.5)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.observations[0].precip_mm_hr == 0.0


def test_breach_row_gets_hazard_polygon_built_from_real_geography_polygon():
    # 25 m/s ~= 55.9mph, well above the 45mph threshold.
    rows = [_row("breach-stop", 32.78, -96.81, _NOW, temp_k=294.26, precip_m=0, wind_ms=25.0)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="breach-stop", location=Coordinate(lat=32.78, lon=-96.81), eta=_NOW)
        ],
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
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=273.15, precip_m=0, wind_ms=2.0)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert result.observations[0].temp_f == 32.0


def test_query_uses_real_schema_shape_not_placeholder():
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
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

    # imerg_tp_1hr, not total_precipitation_1hr: the calibrated field, per Google's own docs.
    assert "imerg_tp_1hr_p90" in sql_text
    assert "total_precipitation_1hr" not in sql_text

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
    interim 1-hourly inits => 48h horizon) — this module's own design addition beyond the
    documented example queries, now confirmed against real hourly inits."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    sql_text = client.query.call_args[0][0]
    assert "MOD(EXTRACT(HOUR FROM ci.init_time), 6) = 0" in sql_text
    assert "<= 360" in sql_text
    assert "<= 48" in sql_text


def test_query_has_static_partition_bound_on_t1():
    """A BigQuery cost/perf guard: the second reference to the partitioned table (t1) must carry
    its own static init_time bound, not rely solely on an equi-join derived from a multi-level CTE
    to be provably partition-prunable to the optimizer. This bound is what actually controls scan
    size (confirmed live: 16 DAY = 7.45TB, 24 HOUR = 363GB, 12 HOUR = 61GB for the same query)."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    sql_text = client.query.call_args[0][0]
    expected_bound = (
        f"ON t1.init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), "
        f"INTERVAL {config.WEATHERNEXT_INIT_LOOKBACK_HOURS} HOUR)"
    )
    assert expected_bound in sql_text
    assert "AND t1.init_time = b.init_time" in sql_text


def test_query_uses_configured_lookback_not_the_old_hardcoded_16_days():
    """The lookback window used to be hardcoded as `INTERVAL 16 DAY` in two places, conflating
    forecast horizon (up to 360h into the future) with init lookback (how far into the past to
    search — inits occur at minimum every 6h, so ~24h is ample). That was the actual root cause of
    a real, measured 8.19TB query, not an inherent dataset property. Now templated from a single
    config value so both occurrences can never drift from each other again."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    sql_text = client.query.call_args[0][0]
    assert "16 DAY" not in sql_text
    assert sql_text.count(f"INTERVAL {config.WEATHERNEXT_INIT_LOOKBACK_HOURS} HOUR") == 2


def test_query_rounds_coordinates_before_partitioning():
    """Defensive, not currently load-bearing: lat/lon flow from one canonical parameter value per
    query today, but rounding before CAST(...AS STRING) removes any future risk of two
    textually-different float representations of "the same" coordinate splitting partitions."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    sql_text = client.query.call_args[0][0]
    assert "ROUND(lat, 5)" in sql_text
    assert "ROUND(lon, 5)" in sql_text
    assert "ROUND(b.lat, 5)" in sql_text
    assert "ROUND(b.lon, 5)" in sql_text


def test_query_has_maximum_bytes_billed_safety_cap():
    """A malformed query or an accidental widening of the lookback window should fail loudly and
    for free instead of silently billing an unbounded amount."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    job_config = client.query.call_args[1]["job_config"]
    assert job_config.maximum_bytes_billed == config.WEATHERNEXT_MAX_BYTES_BILLED
    assert job_config.maximum_bytes_billed > 0


def test_run_weather_check_raises_on_empty_results():
    client = _mock_client([])
    request = _make_request()

    with pytest.raises(RuntimeError) as exc:
        run_weather_check(request, bq_client=client)
    assert "No WeatherNext forecast records found" in str(exc.value)


def test_run_weather_check_detects_unmonitored_waypoints():
    # Only return row for clear-stop; breach-stop has missing data
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    result = run_weather_check(request, bq_client=client)

    assert result.unmonitored_waypoints == ["breach-stop"]
    assert len(result.observations) == 1


def test_duplicate_waypoint_names_at_different_locations_both_get_observations():
    """Two waypoints sharing a generic name ("Rest Stop") but at different coordinates must both
    be tracked independently — partitioning/matching by bare name would silently collapse them."""
    eta_a = _NOW

    eta_b = _NOW + timedelta(hours=2)
    rows = [
        _row("Rest Stop", 29.76, -95.37, eta_a, temp_k=294.26, precip_m=0, wind_ms=4.5),
        _row("Rest Stop", 35.0, -100.0, eta_b, temp_k=294.26, precip_m=0, wind_ms=4.5),
    ]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="Rest Stop", location=Coordinate(lat=29.76, lon=-95.37), eta=eta_a),
            Waypoint(name="Rest Stop", location=Coordinate(lat=35.0, lon=-100.0), eta=eta_b),
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert len(result.observations) == 2
    assert result.unmonitored_waypoints is None


def test_duplicate_waypoint_name_with_one_missing_is_correctly_flagged_unmonitored():
    """The specific failure mode: two same-named waypoints, only one has data. The other must
    still show up in unmonitored_waypoints — a bare-name match would have hidden this."""

    eta_a = _NOW
    eta_b = _NOW + timedelta(hours=2)
    rows = [_row("Rest Stop", 29.76, -95.37, eta_a, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="Rest Stop", location=Coordinate(lat=29.76, lon=-95.37), eta=eta_a),
            Waypoint(name="Rest Stop", location=Coordinate(lat=35.0, lon=-100.0), eta=eta_b),
        ],
    )

    result = run_weather_check(request, bq_client=client)

    assert len(result.observations) == 1
    assert result.unmonitored_waypoints == ["Rest Stop"]


def test_query_uses_minute_level_timestamp_matching():
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)

    sql_text = client.query.call_args[0][0]
    assert "TIMESTAMP_DIFF(forecast.time, b.eta, MINUTE)" in sql_text


def test_cache_hit_skips_bigquery_call_entirely():
    """Repeated checks of the same shipment must not re-pay the real BigQuery cost (confirmed
    live: ~100-500GB per single-point lookup) within the cache TTL."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    first = run_weather_check(request, bq_client=client)
    assert client.query.call_count == 1

    second = run_weather_check(request, bq_client=client)
    assert client.query.call_count == 1  # not called again — served from cache
    assert second == first


def test_cache_expiry_triggers_a_fresh_query():
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)
    request = _make_request()

    run_weather_check(request, bq_client=client)
    assert client.query.call_count == 1

    future = time_module.time() + config.WEATHERNEXT_CACHE_TTL_SECONDS + 1
    with patch("tools.weather.time.time", return_value=future):
        run_weather_check(request, bq_client=client)

    assert client.query.call_count == 2  # cache expired — real query ran again


def test_cache_does_not_collide_across_different_requests():
    """Two different shipments (or the same shipment with different waypoints/cargo) must not
    share a cache entry."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)

    request_a = RouteWeatherRequest(
        shipment_id="ship-a",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )
    request_b = RouteWeatherRequest(
        shipment_id="ship-b",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )

    run_weather_check(request_a, bq_client=client)
    run_weather_check(request_b, bq_client=client)

    assert client.query.call_count == 2


def test_cache_key_rounds_eta_so_near_identical_requests_still_hit():
    """The query itself still uses the precise eta for its nearest-forecast-step match — only the
    cache key is rounded, since a fresh caller (not the UI's session-memoized Shipment) could
    construct "the same logical" request a few minutes apart and should still get a cache hit."""
    rows = [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    client = _mock_client(rows)

    request_first = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
        ],
    )
    request_shifted = RouteWeatherRequest(
        shipment_id="ship-1",
        waypoints=[
            Waypoint(
                name="clear-stop",
                location=Coordinate(lat=29.76, lon=-95.37),
                eta=_NOW + timedelta(minutes=3),
            )
        ],
    )

    run_weather_check(request_first, bq_client=client)
    run_weather_check(request_shifted, bq_client=client)

    assert client.query.call_count == 1  # second call served from cache despite the eta shift


def test_cache_evicts_oldest_entry_once_max_size_exceeded():
    client = _mock_client(
        [_row("clear-stop", 29.76, -95.37, _NOW, temp_k=294.26, precip_m=0, wind_ms=4.5)]
    )

    def request_for(shipment_id):
        return RouteWeatherRequest(
            shipment_id=shipment_id,
            waypoints=[
                Waypoint(name="clear-stop", location=Coordinate(lat=29.76, lon=-95.37), eta=_NOW)
            ],
        )

    with patch("tools.weather._WEATHER_CACHE_MAX_SIZE", 2):
        run_weather_check(request_for("ship-a"), bq_client=client)
        run_weather_check(request_for("ship-b"), bq_client=client)
        assert client.query.call_count == 2

        # A third distinct request pushes the cache over its size-2 cap, evicting "ship-a" (the
        # oldest entry) — re-checking "ship-a" afterward must re-query, not hit a stale entry.
        run_weather_check(request_for("ship-c"), bq_client=client)
        assert client.query.call_count == 3

        run_weather_check(request_for("ship-a"), bq_client=client)
        assert client.query.call_count == 4  # evicted — had to re-query

        run_weather_check(request_for("ship-c"), bq_client=client)
        assert client.query.call_count == 4  # still cached — was not evicted
