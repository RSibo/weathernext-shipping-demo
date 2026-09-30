"""find_detour: candidate routes that can actually differ from the baseline, judged by assess_route.

The old compute_detour asked Routes API for alternatives while passing intermediate stops, which
the API never honours — so the "detour" was always the baseline. Candidates now come from:
  1. Routes API alternatives (only for lanes without stops, where the API returns them), and
  2. curated highway anchors (data/anchors.json) sent as pass-through `via` waypoints, which work
     with stops and steer a route onto a different mainline (I-70 instead of I-80).
Every candidate is assessed on its OWN ETAs; the fastest clean one wins. If none is clean, the
least-exposed one is returned flagged still_hazardous so it can never be presented as safe.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Optional

from schemas import Coordinate, DetourAnalysis, DetourOption, RouteAssessment, RoutePlan, Shipment
from tools.assess import assess_route
from tools.cube import ForecastCube
from tools.geo import haversine_km
from tools.routing import RouteCache, RoutingError, compute_routes

logger = logging.getLogger(__name__)

_ANCHORS_PATH = Path(__file__).resolve().parent.parent / "data" / "anchors.json"
MAX_DETOUR_RATIO = 1.4  # candidate duration / baseline duration, and anchor great-circle ratio
ANCHOR_BREACH_EXCLUSION_KM = 20.0
MAX_ANCHOR_CALLS = 3


@lru_cache(maxsize=1)
def load_anchors() -> tuple[dict, ...]:
    return tuple(json.loads(_ANCHORS_PATH.read_text()))


def option_id(plan: RoutePlan) -> str:
    if plan.route_id.startswith("alt:"):
        return "detour:" + plan.route_id[4:]
    if plan.route_id.startswith("via:"):
        return "detour:via-" + plan.route_id[4:]
    return plan.route_id


def _eligible_anchors(shipment: Shipment, baseline: RouteAssessment, existing: list[RoutePlan]) -> list[dict]:
    o, d = shipment.origin.location, shipment.destination.location
    direct = haversine_km(o.lat, o.lon, d.lat, d.lon)
    breach_pts = [(s.lat, s.lon) for s in baseline.samples if s.status == "breach"]
    scored = []
    for a in load_anchors():
        ratio = (haversine_km(o.lat, o.lon, a["lat"], a["lon"]) + haversine_km(a["lat"], a["lon"], d.lat, d.lon)) / direct
        if ratio > MAX_DETOUR_RATIO:
            continue
        if any(haversine_km(a["lat"], a["lon"], la, lo) < ANCHOR_BREACH_EXCLUSION_KM for la, lo in breach_pts):
            continue
        scored.append((ratio, a))
    scored.sort(key=lambda x: x[0])
    return [a for _, a in scored]


def _near_existing(anchor: dict, plans: list[RoutePlan]) -> bool:
    """Skip an anchor an existing candidate already passes (e.g. the I-70 alternative already goes
    through Grand Junction) — calling it would just return a duplicate."""
    from tools.polyline_codec import decode

    for p in plans:
        pts = decode(p.polyline)[::10]
        if any(haversine_km(anchor["lat"], anchor["lon"], la, lo) < 15 for la, lo in pts):
            return True
    return False


def _via_index(shipment: Shipment, anchor: dict) -> int:
    """Put the via in the leg it detours least from (stops keep their order)."""
    pts = [w.location for w in shipment.all_waypoints()]
    best, best_cost = 0, float("inf")
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        cost = (haversine_km(a.lat, a.lon, anchor["lat"], anchor["lon"])
                + haversine_km(anchor["lat"], anchor["lon"], b.lat, b.lon)
                - haversine_km(a.lat, a.lon, b.lat, b.lon))
        if cost < best_cost:
            best, best_cost = i, cost
    return best


def _dedupe(plans: list[RoutePlan], baseline: RoutePlan) -> list[RoutePlan]:
    out: list[RoutePlan] = []
    seen = [baseline]
    for p in plans:
        dup = any(
            p.polyline == q.polyline
            or (p.description == q.description and abs(p.distance_meters - q.distance_meters) < 0.01 * q.distance_meters)
            for q in seen
        )
        if not dup:
            out.append(p)
            seen.append(p)
    return out


def find_detour(
    shipment: Shipment,
    baseline: RoutePlan,
    baseline_assessment: RouteAssessment,
    cube: ForecastCube,
    *,
    cache: Optional[RouteCache] = None,
    api_key: Optional[str] = None,
    max_anchor_calls: int = MAX_ANCHOR_CALLS,
) -> DetourAnalysis:
    o, d = shipment.origin.location, shipment.destination.location
    stops = [w.location for w in shipment.waypoints]
    depart = shipment.origin.eta
    calls = 0
    plans: list[RoutePlan] = []

    if not stops:
        calls += 1
        try:
            plans += [p for p in compute_routes(o, d, alternatives=True, cache=cache, api_key=api_key) if p.source == "alternative"]
        except RoutingError:
            logger.warning("alternatives call failed", exc_info=True)

    anchors = [a for a in _eligible_anchors(shipment, baseline_assessment, plans) if not _near_existing(a, [baseline, *plans])]
    anchors = anchors[:max_anchor_calls]

    def _call(anchor: dict) -> list[RoutePlan]:
        return compute_routes(
            o, d, stops=stops, via=Coordinate(lat=anchor["lat"], lon=anchor["lon"]),
            via_label=f"{anchor['highway']} via {anchor['name']}", via_index=_via_index(shipment, anchor),
            cache=cache, api_key=api_key,
        )

    if anchors:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_call, a) for a in anchors]
            for f in futures:
                calls += 1
                try:
                    plans += f.result()[:1]
                except RoutingError:
                    # One failed anchor must not sink the whole detour search.
                    logger.warning("anchor route failed", exc_info=True)

    plans = [p for p in _dedupe(plans, baseline) if p.duration_seconds <= MAX_DETOUR_RATIO * baseline.duration_seconds]
    candidates = [
        DetourOption(
            option_id=option_id(p),
            plan=p,
            assessment=assess_route(p, depart, cube, shipment.cargo_description),
            delta_distance_meters=p.distance_meters - baseline.distance_meters,
            delta_duration_seconds=p.duration_seconds - baseline.duration_seconds,
        )
        for p in plans
    ]

    chosen: Optional[DetourOption] = None
    for tier in ("clean", "at_risk"):
        tier_opts = [c for c in candidates if c.assessment.verdict == tier]
        if tier_opts:
            chosen = min(tier_opts, key=lambda c: c.plan.duration_seconds)
            break
    still_hazardous = chosen is None
    if chosen is None:
        # Least-bad: less exposure than the baseline, or nothing at all.
        worse_ok = [c for c in candidates if c.assessment.exposure_score < baseline_assessment.exposure_score]
        if worse_ok:
            chosen = min(worse_ok, key=lambda c: (c.assessment.exposure_score, c.plan.duration_seconds))

    return DetourAnalysis(
        shipment_id=shipment.shipment_id,
        baseline_id=baseline.route_id,
        candidates=candidates,
        chosen_id=chosen.option_id if chosen else None,
        still_hazardous=still_hazardous,
        anchors_tried=[f"{a['highway']} via {a['name']}" for a in anchors],
        routes_calls=calls,
    )
