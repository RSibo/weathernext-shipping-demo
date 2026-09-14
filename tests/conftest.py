from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from schemas import Coordinate, Shipment, Waypoint

CASSETTE_DIR = pathlib.Path(__file__).parent / "cassettes"

_NOW = datetime(
    2026, 1, 1, tzinfo=timezone.utc
)  # fixed, not datetime.now(): cassette requests were
# matched/recorded against a specific prompt text at record time; a fixed clock keeps replay
# requests byte-identical to what's on disk.


@pytest.fixture(autouse=True)
def _reset_weather_cache():
    """Several tests reuse the same waypoint name/coordinates (e.g. "clear-stop" at the same
    lat/lon across many test_weather_tool.py functions) — without this, a later test's mock
    client would never be called because an earlier test's cached result for the "same" request
    key would be returned instead, silently breaking test isolation."""
    from tools.weather import _clear_weather_cache

    _clear_weather_cache()
    yield
    _clear_weather_cache()


@pytest.fixture
def sample_shipment() -> Shipment:
    return Shipment(
        shipment_id="demo-1",
        origin=Waypoint(name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698), eta=_NOW),
        destination=Waypoint(
            name="Dallas",
            location=Coordinate(lat=32.7767, lon=-96.8088),
            eta=_NOW + timedelta(hours=6),
        ),
    )
