"""compute_detour_route: alternative-route computation with hazard-polygon filtering.

Technical correction to the source blueprint: Google's Routes API `computeRoutes` has no
"avoid polygon" parameter — only avoidTolls/avoidHighways/avoidFerries route modifiers. Instead,
this calls computeRoutes with computeAlternativeRoutes=true, decodes each candidate's polyline,
and uses shapely to find candidates whose path does not intersect the hazard polygon(s). The
fastest clean candidate wins; if none is clean, the least-hazardous one is returned, explicitly
flagged still_hazardous=True so Gemini cannot present it as safe.

Uses a raw httpx call against the documented REST endpoint rather than a generated client library
— the v2 REST contract (origin/destination/travelMode/computeAlternativeRoutes + field-mask
header) is simple and stable, and this avoids an unverified extra dependency.

Note: `computeAlternativeRoutes: true` with a wide field mask bills at a higher Routes API SKU
tier — the field mask below is kept minimal.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Literal, Optional, Sequence

import httpx
from shapely.geometry import shape
from shapely.ops import unary_union

import config
from schemas import CandidateRoute, Coordinate, DetourRequest, DetourResult, RouteLeg, RoutePlan
from tools.polyline_codec import to_linestring

_COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"


class RoutingError(RuntimeError):
    """A real Routes API failure (auth, network, bad request) — distinct from "no clear route
    available", which is a successful call with a hazardous result."""


def _call_compute_routes(origin, destination, intermediates=None, *, api_key: str) -> list[dict]:
    body = {
        "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lon}}},
        "destination": {
            "location": {"latLng": {"latitude": destination.lat, "longitude": destination.lon}}
        },
        "travelMode": "DRIVE",
        # Routes API: "No alternative routes are returned for requests that have intermediate
        # waypoints" — asking anyway just returns the one route, so only ask when it can help.
        "computeAlternativeRoutes": not intermediates,
        "routingPreference": "TRAFFIC_AWARE",
    }
    if intermediates:
        body["intermediates"] = [
            {"location": {"latLng": {"latitude": wp.lat, "longitude": wp.lon}}}
            for wp in intermediates
        ]
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    try:
        response = httpx.post(_COMPUTE_ROUTES_URL, json=body, headers=headers, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RoutingError(f"Routes API call failed: {exc}") from exc

    return response.json().get("routes", [])


def _filter_against_hazards(
    candidates: list[CandidateRoute], hazard_polygons: list
) -> list[CandidateRoute]:
    if not hazard_polygons:
        return candidates

    hazard_union = unary_union([shape(hp.geojson) for hp in hazard_polygons])

    filtered = []
    for candidate in candidates:
        linestring = to_linestring(candidate.polyline)
        intersects = linestring.intersects(hazard_union)
        intersection_length = linestring.intersection(hazard_union).length if intersects else 0.0
        filtered.append(
            candidate.model_copy(
                update={
                    "intersects_hazard": intersects,
                    "intersection_length_deg": intersection_length,
                }
            )
        )
    return filtered


def compute_detour(request: DetourRequest, *, api_key: str | None = None) -> DetourResult:
    """The implementation behind the `compute_detour_route` tool."""
    key = api_key if api_key is not None else config.GOOGLE_MAPS_ROUTES_API_KEY
    if not key:
        raise RoutingError("GOOGLE_MAPS_ROUTES_API_KEY is not set")

    raw_routes = _call_compute_routes(
        request.origin,
        request.destination,
        intermediates=request.intermediates,
        api_key=key,
    )
    if not raw_routes:
        raise RoutingError("Routes API returned no candidate routes")

    candidates = [
        CandidateRoute(
            polyline=r["polyline"]["encodedPolyline"],
            distance_meters=r["distanceMeters"],
            duration_seconds=int(r["duration"].rstrip("s")),
            intersects_hazard=False,
        )
        for r in raw_routes
    ]
    # Routes API returns its default (no hazard-avoidance) route first; that's the "as planned"
    # baseline the Route Comparison section's delta is measured against.
    baseline = candidates[0]
    candidates = _filter_against_hazards(candidates, request.hazard_polygons)

    clean = [c for c in candidates if not c.intersects_hazard]
    if clean:
        chosen = min(clean, key=lambda c: c.duration_seconds)
        status = "clear_route_found"
        still_hazardous = False
    else:
        chosen = min(candidates, key=lambda c: c.intersection_length_deg)
        status = "no_clear_route_available"
        still_hazardous = True

    advisory_guidance = None
    if still_hazardous:
        # still_hazardous is only ever True when hazard_polygons was non-empty (an empty hazard
        # list makes every candidate trivially "clean" — see _filter_against_hazards's early
        # return above), so the schema's own model_validator enforces this pairing unconditionally.
        advisory_guidance = (
            "Severe hazard directly encompasses route corridor or waypoint(s). Spatial rerouting cannot bypass "
            "the impact area; dispatcher should hold the vehicle at the prior safe staging area "
            "or delay departure until the weather system clears."
        )

    return DetourResult(
        shipment_id=request.shipment_id,
        status=status,
        chosen_route=chosen,
        all_candidates=candidates,
        still_hazardous=still_hazardous,
        baseline_route=baseline,
        distance_delta_meters=chosen.distance_meters - baseline.distance_meters,
        eta_delta_seconds=chosen.duration_seconds - baseline.duration_seconds,
        advisory_guidance=advisory_guidance,
    )


# --- Showcase routing: compute_routes + RouteCache -------------------------------------------

_PLAN_FIELD_MASK = ",".join([
    "routes.duration",
    "routes.distanceMeters",
    "routes.polyline.encodedPolyline",
    "routes.description",
    "routes.routeLabels",
    "routes.legs.distanceMeters",
    "routes.legs.duration",
])

CacheMode = Literal["live", "replay"]


class RouteCache:
    """Routes API responses keyed by a hash of the request body.

    live: call the API on a miss and store the response. replay: never call the API; a miss is a
    RoutingError. Replay mode is what makes recorded demos deterministic and free, and it is why
    requests are TRAFFIC_UNAWARE with no departureTime (a past departureTime is invalid for DRIVE,
    and traffic makes the same request return different routes minute to minute).
    """

    def __init__(self, directory: Optional[Path] = None, mode: CacheMode = "live"):
        self.directory = Path(directory) if directory else None
        self.mode = mode
        self._mem: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self.live_calls = 0
        self.cached_calls = 0
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    def scoped(self) -> "RouteCache":
        """Same store, fresh counters: one per agent run so concurrent runs report their own calls."""
        child = RouteCache.__new__(RouteCache)
        child.directory, child.mode, child._mem, child._lock = self.directory, self.mode, self._mem, self._lock
        child.live_calls = child.cached_calls = 0
        return child

    def count(self, *, live: bool) -> None:
        with self._lock:
            if live:
                self.live_calls += 1
            else:
                self.cached_calls += 1

    @staticmethod
    def key(body: dict) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]

    def get(self, body: dict) -> Optional[list[dict]]:
        k = self.key(body)
        with self._lock:
            if k in self._mem:
                return self._mem[k]
        if self.directory and (self.directory / f"{k}.json").exists():
            routes = json.loads((self.directory / f"{k}.json").read_text())["routes"]
            with self._lock:
                self._mem[k] = routes
            return routes
        return None

    def put(self, body: dict, routes: list[dict]) -> None:
        k = self.key(body)
        with self._lock:
            self._mem[k] = routes
        if self.directory:
            (self.directory / f"{k}.json").write_text(json.dumps({"request": body, "routes": routes}))


def _latlng(c: Coordinate) -> dict:
    return {"location": {"latLng": {"latitude": round(c.lat, 5), "longitude": round(c.lon, 5)}}}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "route"


def compute_routes(
    origin: Coordinate,
    destination: Coordinate,
    *,
    stops: Sequence[Coordinate] = (),
    via: Optional[Coordinate] = None,
    via_label: Optional[str] = None,
    via_index: Optional[int] = None,
    alternatives: bool = False,
    cache: Optional[RouteCache] = None,
    api_key: Optional[str] = None,
) -> list[RoutePlan]:
    """One computeRoutes call -> RoutePlans. The first plan of a no-via call is the default route.

    `via` is a pass-through (via: true) intermediate: it steers the route along a highway without
    adding a leg, so leg ETAs for real stops stay valid.
    """
    if via is not None and alternatives:
        raise ValueError("alternatives are not returned when intermediates are present")
    intermediates = [_latlng(s) for s in stops]
    if via is not None:
        pos = len(intermediates) if via_index is None else max(0, min(via_index, len(intermediates)))
        intermediates.insert(pos, {**_latlng(via), "via": True})
    body: dict = {
        "origin": _latlng(origin),
        "destination": _latlng(destination),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
        "computeAlternativeRoutes": bool(alternatives and not intermediates),
        "units": "IMPERIAL",
    }
    if intermediates:
        body["intermediates"] = intermediates

    raw = cache.get(body) if cache else None
    if raw is not None:
        cache.count(live=False)
    else:
        if cache and cache.mode == "replay":
            raise RoutingError("route not in the recorded cache (replay mode)")
        key = api_key if api_key is not None else config.GOOGLE_MAPS_ROUTES_API_KEY
        if not key:
            raise RoutingError("GOOGLE_MAPS_ROUTES_API_KEY is not set")
        headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": _PLAN_FIELD_MASK}
        try:
            response = httpx.post(_COMPUTE_ROUTES_URL, json=body, headers=headers, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RoutingError(f"Routes API call failed: {exc}") from exc
        raw = response.json().get("routes", [])
        if cache:
            cache.count(live=True)
            cache.put(body, raw)

    plans = []
    for i, r in enumerate(raw):
        description = r.get("description") or "Route"
        if via is not None:
            source, route_id = "via_anchor", f"via:{slug(via_label or description)}"
        elif i == 0:
            source, route_id = "default", "baseline"
        else:
            source, route_id = "alternative", f"alt:{slug(description)}"
        plans.append(
            RoutePlan(
                route_id=route_id,
                description=description,
                labels=r.get("routeLabels", []),
                source=source,
                via_label=via_label,
                distance_meters=int(r.get("distanceMeters", 0)),
                duration_seconds=int(str(r.get("duration", "0s")).rstrip("s")),
                polyline=r["polyline"]["encodedPolyline"],
                legs=[
                    RouteLeg(
                        distance_meters=int(leg.get("distanceMeters", 0)),
                        duration_seconds=int(str(leg.get("duration", "0s")).rstrip("s")),
                    )
                    for leg in r.get("legs", [])
                ],
            )
        )
    return plans
