"""One-off script to (re)record the agent loop cassettes against a real, credentialed, billed
Gemini call. Not a pytest test itself — run manually:

    python -m tests._record_cassettes

Then sanitize before committing (see bottom of this file's __main__ block).
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import config
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
from tests._vendor.genai_cassette import genai_cassette, sanitize_cassette

CASSETTE_DIR = pathlib.Path(__file__).parent / "cassettes"

# Fixed, not datetime.now(): the replay client asserts the request body matches byte-for-byte
# against what's on disk, and the shipment summary embeds these timestamps in the prompt text.
# Must match tests/conftest.py's sample_shipment fixture exactly.
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clear_shipment() -> Shipment:
    return Shipment(
        shipment_id="demo-1",
        origin=Waypoint(name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698), eta=NOW),
        destination=Waypoint(
            name="Dallas", location=Coordinate(lat=32.7767, lon=-96.8088), eta=NOW + timedelta(hours=6)
        ),
    )


def _clear_weather_result() -> RouteWeatherResult:
    return RouteWeatherResult(
        shipment_id="demo-1",
        observations=[
            WeatherObservation(
                waypoint_name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698),
                valid_time=NOW, wind_speed_mph=10, precip_mm_hr=0, temp_f=75, breached=False,
            ),
            WeatherObservation(
                waypoint_name="Dallas", location=Coordinate(lat=32.7767, lon=-96.8088),
                valid_time=NOW, wind_speed_mph=12, precip_mm_hr=0, temp_f=70, breached=False,
            ),
        ],
    )


def _breach_shipment() -> Shipment:
    return Shipment(
        shipment_id="demo-2",
        origin=Waypoint(name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698), eta=NOW),
        destination=Waypoint(
            name="Dallas", location=Coordinate(lat=32.7767, lon=-96.8088), eta=NOW + timedelta(hours=6)
        ),
    )


def _breach_weather_result() -> RouteWeatherResult:
    hazard = HazardPolygon(
        geojson={
            "type": "Polygon",
            "coordinates": [[[-96.9, 32.7], [-96.7, 32.7], [-96.7, 32.9], [-96.9, 32.9], [-96.9, 32.7]]],
        },
        waypoint_name="Dallas",
        reason_summary="Wind 58.0mph exceeds 45mph threshold",
    )
    return RouteWeatherResult(
        shipment_id="demo-2",
        observations=[
            WeatherObservation(
                waypoint_name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698),
                valid_time=NOW, wind_speed_mph=10, precip_mm_hr=0, temp_f=75, breached=False,
            ),
            WeatherObservation(
                waypoint_name="Dallas", location=Coordinate(lat=32.7767, lon=-96.8088),
                valid_time=NOW, wind_speed_mph=58, precip_mm_hr=0, temp_f=70, breached=True,
                reasons=["Wind 58.0mph exceeds 45mph threshold"], hazard_polygon=hazard,
            ),
        ],
    )


def _breach_detour_result() -> DetourResult:
    baseline = CandidateRoute(polyline="orig", distance_meters=230000, duration_seconds=9600, intersects_hazard=True)
    chosen = CandidateRoute(polyline="abc", distance_meters=250000, duration_seconds=10800, intersects_hazard=False)
    return DetourResult(
        shipment_id="demo-2", status="clear_route_found", chosen_route=chosen,
        all_candidates=[baseline, chosen], still_hazardous=False, baseline_route=baseline,
        distance_delta_meters=chosen.distance_meters - baseline.distance_meters,
        eta_delta_seconds=chosen.duration_seconds - baseline.duration_seconds,
    )


def record_clear():
    from google import genai

    cassette = CASSETTE_DIR / "agent" / "run_advisory_clear" / "vertex.json"
    with patch("agent.loop.run_weather_check", return_value=_clear_weather_result()):
        with genai_cassette("record", cassette):
            import agent.loop as loop

            client = genai.Client(vertexai=True, project=config.PROJECT_ID, location=config.VERTEX_LOCATION)
            try:
                text = loop.run_advisory(_clear_shipment(), client=client)
            finally:
                # Recorded interactions are buffered in memory and only flushed to disk on
                # close() (google/genai/_replay_api_client.py's `close()` is what writes the file).
                client.close()
    print("CLEAR:", text)
    sanitize_cassette(cassette, project_id=config.PROJECT_ID)


def record_breach():
    from google import genai

    cassette = CASSETTE_DIR / "agent" / "run_advisory_breach" / "vertex.json"
    with patch("agent.loop.run_weather_check", return_value=_breach_weather_result()), patch(
        "agent.loop.compute_detour", return_value=_breach_detour_result()
    ):
        with genai_cassette("record", cassette):
            import agent.loop as loop

            client = genai.Client(vertexai=True, project=config.PROJECT_ID, location=config.VERTEX_LOCATION)
            try:
                text = loop.run_advisory(_breach_shipment(), client=client)
            finally:
                client.close()
    print("BREACH:", text)
    sanitize_cassette(cassette, project_id=config.PROJECT_ID)


if __name__ == "__main__":
    record_clear()
    record_breach()
