from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from schemas import Coordinate, Shipment, Waypoint

CASSETTE_DIR = pathlib.Path(__file__).parent / "cassettes"

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)  # fixed, not datetime.now(): cassette requests were
# matched/recorded against a specific prompt text at record time; a fixed clock keeps replay
# requests byte-identical to what's on disk.


@pytest.fixture
def sample_shipment() -> Shipment:
    return Shipment(
        shipment_id="demo-1",
        origin=Waypoint(name="Houston", location=Coordinate(lat=29.7604, lon=-95.3698), eta=_NOW),
        destination=Waypoint(
            name="Dallas", location=Coordinate(lat=32.7767, lon=-96.8088), eta=_NOW + timedelta(hours=6)
        ),
    )
