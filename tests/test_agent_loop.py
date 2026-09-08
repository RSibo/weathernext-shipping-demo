from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent.loop import _AdvisoryRun
from agent.system_instruction import SYSTEM_INSTRUCTION
from schemas import (
    CandidateRoute,
    Coordinate,
    DetourResult,
    HazardPolygon,
    RouteWeatherResult,
    Shipment,
    Waypoint,
    WeatherObservation,
)
from tests._vendor.genai_cassette import assert_cassette_is_sanitized, genai_cassette

CASSETTE_DIR = pathlib.Path(__file__).parent / "cassettes"
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)  # must match tests/_record_cassettes.py exactly


def _breach_shipment() -> Shipment:
    return Shipment(
        shipment_id="demo-2",
        origin=Waypoint(name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698), eta=_NOW),
        destination=Waypoint(
            name="Dallas",
            location=Coordinate(lat=32.7767, lon=-96.8088),
            eta=_NOW + timedelta(hours=6),
        ),
    )


def _breach_weather_result() -> RouteWeatherResult:
    hazard = HazardPolygon(
        geojson={
            "type": "Polygon",
            "coordinates": [
                [[-96.9, 32.7], [-96.7, 32.7], [-96.7, 32.9], [-96.9, 32.9], [-96.9, 32.7]]
            ],
        },
        waypoint_name="Dallas",
        reason_summary="Wind 58.0mph exceeds 45mph threshold",
    )
    return RouteWeatherResult(
        shipment_id="demo-2",
        observations=[
            WeatherObservation(
                waypoint_name="Houston",
                location=Coordinate(lat=29.7604, lon=-95.3698),
                valid_time=_NOW,
                wind_speed_mph=10,
                precip_mm_hr=0,
                temp_f=75,
                breached=False,
            ),
            WeatherObservation(
                waypoint_name="Dallas",
                location=Coordinate(lat=32.7767, lon=-96.8088),
                valid_time=_NOW,
                wind_speed_mph=58,
                precip_mm_hr=0,
                temp_f=70,
                breached=True,
                reasons=["Wind 58.0mph exceeds 45mph threshold"],
                hazard_polygon=hazard,
            ),
        ],
    )


def _breach_detour_result() -> DetourResult:
    baseline = CandidateRoute(
        polyline="orig", distance_meters=230000, duration_seconds=9600, intersects_hazard=True
    )
    chosen = CandidateRoute(
        polyline="abc", distance_meters=250000, duration_seconds=10800, intersects_hazard=False
    )
    return DetourResult(
        shipment_id="demo-2",
        status="clear_route_found",
        chosen_route=chosen,
        all_candidates=[baseline, chosen],
        still_hazardous=False,
        baseline_route=baseline,
        distance_delta_meters=chosen.distance_meters - baseline.distance_meters,
        eta_delta_seconds=chosen.duration_seconds - baseline.duration_seconds,
    )


def test_guardrail_blocks_detour_before_weather_check(sample_shipment):
    """Pure Python, no cassette needed: proves the Mandatory Rule #1 guardrail is enforced by the
    loop's own dispatcher, independent of whether Gemini ever tries to violate it."""
    run = _AdvisoryRun(sample_shipment)

    result = run.dispatch("compute_detour_route", {"shipment_id": sample_shipment.shipment_id})

    assert "error" in result
    assert "Mandatory rule violated" in result["error"]


def test_guardrail_blocks_detour_when_weather_was_clear(sample_shipment):
    run = _AdvisoryRun(sample_shipment)
    clear_result = RouteWeatherResult(
        shipment_id=sample_shipment.shipment_id,
        observations=[
            WeatherObservation(
                waypoint_name="Houston",
                location=Coordinate(lat=29.76, lon=-95.37),
                valid_time=_NOW,
                wind_speed_mph=5,
                precip_mm_hr=0,
                temp_f=70,
                breached=False,
            )
        ],
    )
    run.last_weather_result = clear_result

    result = run.dispatch("compute_detour_route", {"shipment_id": sample_shipment.shipment_id})

    assert "error" in result
    assert "any_breach=false" in result["error"]


def test_weather_result_override_suppresses_real_weather_check(sample_shipment):
    """Powers the UI's Simulation Mode (ui/app.py) — proves the override actually replaces the
    real BigQuery call rather than just being ignored."""
    override_result = RouteWeatherResult(shipment_id=sample_shipment.shipment_id, observations=[])
    run = _AdvisoryRun(sample_shipment, weather_result_override=override_result)

    with patch("agent.loop.run_weather_check") as mocked:
        response = run.dispatch("check_route_weather", {"shipment_id": sample_shipment.shipment_id})

    assert not mocked.called
    assert run.last_weather_result is override_result
    assert response["result"]["shipment_id"] == sample_shipment.shipment_id


def test_dispatch_rejects_mismatched_shipment_id(sample_shipment):
    run = _AdvisoryRun(sample_shipment)
    result = run.dispatch("check_route_weather", {"shipment_id": "some-other-shipment"})
    assert "error" in result
    assert "does not match" in result["error"]


def test_unknown_tool_name_returns_error(sample_shipment):
    run = _AdvisoryRun(sample_shipment)
    result = run.dispatch("delete_all_shipments", {"shipment_id": sample_shipment.shipment_id})
    assert "error" in result


@pytest.mark.parametrize("cassette_name", ["run_advisory_clear", "run_advisory_breach"])
def test_cassettes_are_sanitized(cassette_name):
    """Commit-time gate: fails loudly if a cassette was re-recorded without sanitizing."""
    import config

    cassette_path = CASSETTE_DIR / "agent" / cassette_name / "vertex.json"
    assert_cassette_is_sanitized(cassette_path, project_id=config.PROJECT_ID)


def test_run_advisory_clear_replays_status_clear(sample_shipment):
    import agent.loop as loop

    cassette = CASSETTE_DIR / "agent" / "run_advisory_clear" / "vertex.json"
    clear_result = RouteWeatherResult(
        shipment_id=sample_shipment.shipment_id,
        observations=[
            WeatherObservation(
                waypoint_name="Houston",
                location=Coordinate(lat=29.7604, lon=-95.3698),
                valid_time=_NOW,
                wind_speed_mph=10,
                precip_mm_hr=0,
                temp_f=75,
                breached=False,
            ),
            WeatherObservation(
                waypoint_name="Dallas",
                location=Coordinate(lat=32.7767, lon=-96.8088),
                valid_time=_NOW,
                wind_speed_mph=12,
                precip_mm_hr=0,
                temp_f=70,
                breached=False,
            ),
        ],
    )

    with patch("agent.loop.run_weather_check", return_value=clear_result):
        with genai_cassette("replay", cassette):
            from google import genai

            client = genai.Client(vertexai=True, project="placeholder", location="global")
            text = loop.run_advisory(sample_shipment, client=client)

    assert "STATUS: CLEAR" in text


def test_run_advisory_breach_replays_full_advisory():
    import agent.loop as loop

    cassette = CASSETTE_DIR / "agent" / "run_advisory_breach" / "vertex.json"
    shipment = _breach_shipment()

    with (
        patch("agent.loop.run_weather_check", return_value=_breach_weather_result()),
        patch("agent.loop.compute_detour", return_value=_breach_detour_result()),
    ):
        with genai_cassette("replay", cassette):
            from google import genai

            client = genai.Client(vertexai=True, project="placeholder", location="global")
            text = loop.run_advisory(shipment, client=client)

    assert "Hazard Identified" in text
    assert "Route Comparison" in text
    assert "Recommended Action" in text


def test_system_instruction_matches_what_cassettes_were_recorded_against():
    # A cassette recorded against an old prompt "passing" against a new prompt proves nothing —
    # this pins the exact text so prompt edits force a deliberate re-record, not a silent drift.
    assert "MANDATORY RULES" in SYSTEM_INSTRUCTION
    assert "Both tools take only a shipment_id" in SYSTEM_INSTRUCTION
