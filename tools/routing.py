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

import httpx
from shapely.geometry import shape
from shapely.ops import unary_union

import config
from schemas import CandidateRoute, DetourRequest, DetourResult
from tools.polyline_codec import to_linestring

_COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"


class RoutingError(RuntimeError):
    """A real Routes API failure (auth, network, bad request) — distinct from "no clear route
    available", which is a successful call with a hazardous result."""


def _call_compute_routes(origin, destination, *, api_key: str) -> list[dict]:
    body = {
        "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lon}}},
        "destination": {"location": {"latLng": {"latitude": destination.lat, "longitude": destination.lon}}},
        "travelMode": "DRIVE",
        "computeAlternativeRoutes": True,
        "routingPreference": "TRAFFIC_AWARE",
    }
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


def _filter_against_hazards(candidates: list[CandidateRoute], hazard_polygons: list) -> list[CandidateRoute]:
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
                update={"intersects_hazard": intersects, "intersection_length_deg": intersection_length}
            )
        )
    return filtered


def compute_detour(request: DetourRequest, *, api_key: str | None = None) -> DetourResult:
    """The implementation behind the `compute_detour_route` tool."""
    key = api_key if api_key is not None else config.GOOGLE_MAPS_ROUTES_API_KEY
    if not key:
        raise RoutingError("GOOGLE_MAPS_ROUTES_API_KEY is not set")

    raw_routes = _call_compute_routes(request.origin, request.destination, api_key=key)
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

    return DetourResult(
        shipment_id=request.shipment_id,
        status=status,
        chosen_route=chosen,
        all_candidates=candidates,
        still_hazardous=still_hazardous,
        baseline_route=baseline,
        distance_delta_meters=chosen.distance_meters - baseline.distance_meters,
        eta_delta_seconds=chosen.duration_seconds - baseline.duration_seconds,
    )
