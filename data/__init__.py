"""Demo fleet loader.

demo_fleet.json stores ETAs and deadlines as hour offsets, not absolute timestamps: an absolute
date would fall outside the forecast's finite horizon (nothing to sample) and look stale. Offsets
are resolved against a reference time at load: the replayed forecast's story "now"
(demo_fleet_now), or the current time.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from schemas import Coordinate, Shipment, Waypoint

_DEMO_FLEET_PATH = pathlib.Path(__file__).parent / "demo_fleet.json"


def _waypoint_from_dict(raw: dict, reference_time: datetime) -> Waypoint:
    return Waypoint(
        name=raw["name"],
        location=Coordinate(lat=raw["lat"], lon=raw["lon"]),
        eta=reference_time + timedelta(hours=raw["eta_offset_hours"]),
    )


def demo_fleet_now(forecast_init: datetime) -> datetime:
    """The story's 'now' for a replayed forecast: init + replay_now_offset_hours (06Z init -> 16Z,
    10 am in the Mountain West), so every departure sits early in the 48 h forecast window."""
    raw = json.loads(_DEMO_FLEET_PATH.read_text())
    return forecast_init + timedelta(hours=raw.get("replay_now_offset_hours", 0))


def load_demo_fleet(*, reference_time: Optional[datetime] = None) -> list[Shipment]:
    """Loads data/demo_fleet.json, resolving every offset (ETAs and deadlines) against reference_time."""
    reference_time = reference_time if reference_time is not None else datetime.now(timezone.utc)
    raw = json.loads(_DEMO_FLEET_PATH.read_text())
    shipments = []
    for s in raw["shipments"]:
        deadline = s.get("deadline_offset_hours")
        shipments.append(
            Shipment(
                shipment_id=s["shipment_id"],
                carrier=s.get("carrier"),
                cargo_description=s.get("cargo_description"),
                timezone=s.get("timezone", "UTC"),
                origin=_waypoint_from_dict(s["origin"], reference_time),
                waypoints=[_waypoint_from_dict(wp, reference_time) for wp in s.get("waypoints", [])],
                destination=_waypoint_from_dict(s["destination"], reference_time),
                delivery_deadline=reference_time + timedelta(hours=deadline) if deadline is not None else None,
            )
        )
    return shipments
