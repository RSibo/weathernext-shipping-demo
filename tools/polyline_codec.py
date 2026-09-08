"""Decode Google's encoded polyline format into shapely geometry.

The `polyline` package's decode() returns (lat, lon) tuples. shapely/GeoJSON convention is
(x, y) == (lon, lat). This swap is the single easiest silent bug in this file — it gets its own
named function and its own test rather than being inlined ad hoc in tools/routing.py.
"""

from __future__ import annotations

import polyline as _polyline
from shapely.geometry import LineString


def decode(encoded: str) -> list[tuple[float, float]]:
    """Returns (lat, lon) pairs, matching the `polyline` package's own convention."""
    return _polyline.decode(encoded)


def to_linestring(encoded: str) -> LineString:
    """Returns a shapely LineString in (lon, lat) order, ready to compare against GeoJSON polygons."""
    lat_lon_points = decode(encoded)
    lon_lat_points = [(lon, lat) for lat, lon in lat_lon_points]
    return LineString(lon_lat_points)
