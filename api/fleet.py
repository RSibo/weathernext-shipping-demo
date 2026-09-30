"""Deterministic fleet scan: every demo shipment's planned route checked against the forecast.

No LLM and no decisions: the same assess_route the agent's first tool uses, run for the whole fleet
so the map opens with every truck, its route and its risk already drawn. The agent (POST /api/runs)
is what turns a hazardous verdict into REROUTE or HOLD.
"""

from __future__ import annotations

import time
from typing import Literal, Optional

from pydantic import BaseModel

from agent import events as ev
from agent.views import assessment_view, provenance, route_view, samples_view, shipment_view
from api.state import Scene
from tools.assess import assess_route
from tools.facts import FactSheet, weather_payload
from tools.routing import RouteCache, RoutingError, compute_routes

Hint = Literal["CLEAR", "MONITOR", "ACTION", "UNKNOWN"]
_HINT: dict[str, Hint] = {"clean": "CLEAR", "at_risk": "MONITOR", "unmonitored": "MONITOR", "hazardous": "ACTION"}


class FleetEntry(BaseModel):
    shipment: ev.ShipmentView
    hint: Hint
    route: Optional[ev.RouteView] = None
    assessment: Optional[ev.AssessmentView] = None
    samples: list[ev.SampleView] = []
    first_breach: Optional[str] = None  # "12:52–14:48 MDT near Point of Rocks, WY"
    error: Optional[str] = None


class Fleet(BaseModel):
    mode: str
    provenance: ev.Provenance
    now: str  # story time, e.g. "Thu 10:00 MDT"
    now_iso: str
    summary: str  # "2 need action · 1 monitor · 2 clear"
    scan_ms: int
    entries: list[FleetEntry]


def _entry(scene: Scene, shipment_id: str, routes: RouteCache) -> FleetEntry:
    s = scene.shipments[shipment_id]
    fs = FactSheet(s)
    try:
        plans = compute_routes(
            s.origin.location, s.destination.location, stops=[w.location for w in s.waypoints], cache=routes
        )
    except RoutingError as exc:
        return FleetEntry(shipment=shipment_view(fs), hint="UNKNOWN", error=str(exc))
    if not plans:
        return FleetEntry(shipment=shipment_view(fs), hint="UNKNOWN", error="no route for this lane")
    plan = plans[0]
    a = assess_route(plan, s.origin.eta, scene.cube, s.cargo_description)
    weather_payload(fs, plan, a, scene.cube.meta.label)
    view = assessment_view(fs, a)
    first = view.segments[0] if view.segments else None
    return FleetEntry(
        shipment=shipment_view(fs), hint=_HINT[a.verdict], route=route_view(fs, plan, s.origin.eta),
        assessment=view, samples=samples_view(a),
        first_breach=f"{first.window} near {first.place}" if first else None,
    )


def _summary(entries: list[FleetEntry]) -> str:
    n = {h: sum(e.hint == h for e in entries) for h in ("ACTION", "MONITOR", "CLEAR", "UNKNOWN")}
    parts = [
        f"{n['ACTION']} need{'s' if n['ACTION'] == 1 else ''} action" if n["ACTION"] else "",
        f"{n['MONITOR']} monitor" if n["MONITOR"] else "",
        f"{n['CLEAR']} clear" if n["CLEAR"] else "",
        f"{n['UNKNOWN']} unavailable" if n["UNKNOWN"] else "",
    ]
    return " · ".join(p for p in parts if p)


def scan_fleet(scene: Scene, routes: RouteCache) -> Fleet:
    """Cached per scene: the forecast and the fleet are both fixed for a scene's lifetime."""
    with scene.fleet_lock:
        if scene.fleet is None:
            t0 = time.monotonic()
            entries = [_entry(scene, sid, routes) for sid in scene.shipments]
            any_fs = FactSheet(next(iter(scene.shipments.values())))
            scene.fleet = Fleet(
                mode=scene.mode, provenance=provenance(scene.cube.meta),
                now=any_fs.time(scene.now, weekday=True), now_iso=scene.now.isoformat().replace("+00:00", "Z"),
                summary=_summary(entries), scan_ms=int((time.monotonic() - t0) * 1000), entries=entries,
            )
        return scene.fleet
