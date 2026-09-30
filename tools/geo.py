"""Route geometry: resampling a polyline at a fixed spacing, ETAs along it, nearest named place.

Spacing matters: a 0.1deg WeatherNext cell is ~8.5 km wide at 41N, so sampling every 15 km would
step over whole cells. assess_route samples every <= 5 km, so every cell a route crosses is seen.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import numpy as np

from schemas import RouteLeg

EARTH_RADIUS_KM = 6371.0088
_PLACES_PATH = Path(__file__).resolve().parent.parent / "data" / "places.json"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _cumulative_km(points: np.ndarray) -> np.ndarray:
    lat = np.radians(points[:, 0])
    lon = np.radians(points[:, 1])
    dlat = np.diff(lat)
    dlon = np.diff(lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return np.concatenate([[0.0], np.cumsum(seg)])


def resample(points: Sequence[tuple[float, float]], step_km: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Resample a (lat, lon) path at uniform spacing <= step_km, always keeping both endpoints.

    Returns (points[N, 2], km[N]) where km is distance along the ORIGINAL path, so sample km
    values line up with the real route length (not the length of the resampled chord path).
    """
    pts = np.asarray(points, dtype=float)
    if len(pts) == 0:
        raise ValueError("empty path")
    if len(pts) == 1:
        return pts.copy(), np.array([0.0])
    cum = _cumulative_km(pts)
    total = float(cum[-1])
    n = max(2, int(math.ceil(total / step_km)) + 1)
    km = np.linspace(0.0, total, n)
    lat = np.interp(km, cum, pts[:, 0])
    lon = np.interp(km, cum, pts[:, 1])
    return np.column_stack([lat, lon]), km


def path_length_km(points: Sequence[tuple[float, float]]) -> float:
    pts = np.asarray(points, dtype=float)
    return float(_cumulative_km(pts)[-1]) if len(pts) > 1 else 0.0


def etas_along(
    km: np.ndarray,
    path_km: float,
    depart_at: datetime,
    duration_s: int,
    legs: Sequence[RouteLeg] = (),
) -> list[datetime]:
    """ETA at each sample, by distance fraction within each leg (constant speed per leg).

    Leg boundaries come from Routes API legs (distance + duration); with no legs (or one) the whole
    route is one leg. Polyline length and Routes' distanceMeters differ by a few percent, so leg
    boundaries are placed proportionally on the polyline rather than at absolute km values.
    """
    if not legs or len(legs) == 1:
        legs = [RouteLeg(distance_meters=max(1, int(path_km * 1000)), duration_seconds=duration_s)]
    total_m = sum(leg.distance_meters for leg in legs) or 1
    # Boundaries in polyline-km and in seconds since departure.
    km_bounds = [0.0]
    s_bounds = [0.0]
    for leg in legs:
        km_bounds.append(km_bounds[-1] + path_km * leg.distance_meters / total_m)
        s_bounds.append(s_bounds[-1] + leg.duration_seconds)
    secs = np.interp(km, km_bounds, s_bounds)
    return [depart_at + timedelta(seconds=float(s)) for s in secs]


@lru_cache(maxsize=1)
def _places() -> tuple[tuple[str, float, float], ...]:
    raw = json.loads(_PLACES_PATH.read_text())
    return tuple((p["name"], p["lat"], p["lon"]) for p in raw)


def nearest_place(lat: float, lon: float, max_km: float = 60.0) -> str:
    """Human label for a point: 'near Arlington, WY' style, from data/places.json."""
    best_name, best_d = None, float("inf")
    for name, plat, plon in _places():
        d = haversine_km(lat, lon, plat, plon)
        if d < best_d:
            best_name, best_d = name, d
    if best_name is None or best_d > max_km:
        return f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.1f}°{'W' if lon < 0 else 'E'}"
    return best_name


def simplify_path(points: Sequence[tuple[float, float]], tolerance_deg: float = 0.002) -> list[list[float]]:
    """(lat, lon) path -> simplified [[lon, lat], ...] for the wire (deck.gl order)."""
    from shapely.geometry import LineString

    if len(points) < 3:
        return [[round(lon, 5), round(lat, 5)] for lat, lon in points]
    line = LineString([(lon, lat) for lat, lon in points]).simplify(tolerance_deg, preserve_topology=False)
    return [[round(x, 5), round(y, 5)] for x, y in line.coords]
