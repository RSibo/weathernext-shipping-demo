"""Tiny deterministic forecast cubes and straight-line routes for pipeline tests (no GCP)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import polyline as polyline_lib

from schemas import Coordinate, RoutePlan, Shipment, Waypoint
from tools.cube import CubeMeta, ForecastCube

T0 = datetime(2026, 3, 12, 6, tzinfo=timezone.utc)
LAT = np.round(np.arange(40.0, 42.01, 0.1), 2)
LON = np.round(np.arange(-112.0, -103.99, 0.1), 2)


def make_cube(
    *,
    hours: int = 24,
    calm_mph: float = 10.0,
    storm_mph: float = 45.0,
    storm_box: tuple[float, float, float, float] = (-108.0, 41.0, -106.0, 42.0),
    storm_hours: tuple[int, int] = (2, 10),
    precip: float = 0.0,
    temp_f: float = 50.0,
) -> ForecastCube:
    times = np.array([int((T0 + timedelta(hours=h)).timestamp()) for h in range(hours)], dtype=np.int64)
    shape = (hours, len(LAT), len(LON))
    wind = np.full(shape, calm_mph, dtype=np.float32)
    w, s, e, n = storm_box
    ys = (LAT >= s) & (LAT <= n)
    xs = (LON >= w) & (LON <= e)
    for h in range(hours):
        if storm_hours[0] <= h <= storm_hours[1]:
            wind[h][np.ix_(ys, xs)] = storm_mph
    meta = CubeMeta(source="scenario", init_time=T0, bbox=(LON[0], LAT[0], LON[-1], LAT[-1]))
    return ForecastCube(meta, LAT, LON, times, wind, np.full(shape, precip, np.float32), np.full(shape, temp_f, np.float32))


def line_plan(route_id: str, lat: float, lon0: float = -111.0, lon1: float = -105.0, *, hours: float = 6.0,
              description: str = "I-80 E", source: str = "default", via_label: str | None = None) -> RoutePlan:
    pts = [(lat, lon0 + (lon1 - lon0) * i / 60) for i in range(61)]
    return RoutePlan(
        route_id=route_id, description=description, source=source, via_label=via_label,
        distance_meters=int(abs(lon1 - lon0) * 84_000), duration_seconds=int(hours * 3600),
        polyline=polyline_lib.encode(pts),
    )


def shipment(cargo: str = "Empty 53' dry van", depart_h: float = 0.0, deadline_h: float | None = 10.0,
             stops: list[tuple[float, float]] | None = None) -> Shipment:
    dep = T0 + timedelta(hours=depart_h)
    return Shipment(
        shipment_id="T-1",
        cargo_description=cargo,
        timezone="America/Denver",
        origin=Waypoint(name="West", location=Coordinate(lat=41.5, lon=-111.0), eta=dep),
        waypoints=[Waypoint(name=f"Stop {i}", location=Coordinate(lat=a, lon=b), eta=dep + timedelta(hours=1))
                   for i, (a, b) in enumerate(stops or [])],
        destination=Waypoint(name="East", location=Coordinate(lat=41.5, lon=-105.0), eta=dep + timedelta(hours=6)),
        delivery_deadline=dep + timedelta(hours=deadline_h) if deadline_h is not None else None,
    )
