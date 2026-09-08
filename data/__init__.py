"""Mock shipment loader.

mock_shipments.json stores ETAs as eta_offset_hours (relative), not absolute timestamps —
hardcoding an absolute date would make the demo look stale immediately and, worse, guarantee
check_route_weather's time-window join against WeatherNext 3 (whose forecast horizon is finite,
not months into the future) returns nothing. Offsets are resolved to now + N hours at load time.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from schemas import Coordinate, Shipment, Waypoint

_MOCK_SHIPMENTS_PATH = pathlib.Path(__file__).parent / "mock_shipments.json"


def _waypoint_from_dict(raw: dict, reference_time: datetime) -> Waypoint:
    return Waypoint(
        name=raw["name"],
        location=Coordinate(lat=raw["lat"], lon=raw["lon"]),
        eta=reference_time + timedelta(hours=raw["eta_offset_hours"]),
    )


def load_mock_shipments(*, reference_time: Optional[datetime] = None) -> list[Shipment]:
    """Loads data/mock_shipments.json, resolving eta_offset_hours against reference_time
    (defaults to now, UTC) so demo ETAs always fall within WeatherNext 3's forecast horizon."""
    reference_time = reference_time if reference_time is not None else datetime.now(timezone.utc)
    raw_shipments = json.loads(_MOCK_SHIPMENTS_PATH.read_text())

    shipments = []
    for raw in raw_shipments:
        shipments.append(
            Shipment(
                shipment_id=raw["shipment_id"],
                carrier=raw.get("carrier"),
                cargo_description=raw.get("cargo_description"),
                origin=_waypoint_from_dict(raw["origin"], reference_time),
                waypoints=[_waypoint_from_dict(wp, reference_time) for wp in raw["waypoints"]],
                destination=_waypoint_from_dict(raw["destination"], reference_time),
            )
        )
    return shipments
