"""assess_route: the ONE function that decides whether a route is safe at a given departure time.

Baseline checks, every detour candidate, every departure-delay option and the fleet scan all go
through here, so "the detour is clean" and "the baseline breaches" are judged by identical rules.

Method: resample the route every <= 5 km, give each sample its own ETA (the truck reaches Laramie
hours after leaving Salt Lake City — a storm that has passed by then is not a hazard), read the
forecast cube over ETA +/- 1 h at that cell, and run the unchanged tools.thresholds rules on every
forecast hour in that window. Samples the cube cannot see are UNMONITORED, never clean.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import shapely.geometry
from shapely.ops import unary_union

from schemas import BreachSegment, RouteAssessment, RoutePlan, RouteSample
from tools.cube import ForecastCube
from tools.geo import etas_along, nearest_place, path_length_km, resample
from tools.polyline_codec import decode
from tools.thresholds import (
    PRECIP_THRESHOLD_MM_HR,
    evaluate_breach,
    near_threshold,
    wind_threshold_for,
)

SAMPLE_STEP_KM = 5.0
ETA_WINDOW_H = 1.0
# More than this share of unmonitored samples and a route cannot be called clean.
MAX_UNMONITORED_FRACTION = 0.02
# Consecutive flagged samples separated by at most this many clear samples merge into one segment,
# so a single calm cell inside a storm band doesn't split it into two "breaches".
MERGE_GAP_SAMPLES = 2


def _hazard_kinds(reasons: list[str]) -> list[str]:
    kinds = []
    for r in reasons:
        if r.startswith("Wind") and "wind" not in kinds:
            kinds.append("wind")
        elif r.startswith("Precipitation") and "precip" not in kinds:
            kinds.append("precip")
        elif r.startswith("Black ice") and "black_ice" not in kinds:
            kinds.append("black_ice")
    return kinds


def _severity(wind: float, precip: float, threshold: float) -> float:
    return max(wind / threshold, precip / PRECIP_THRESHOLD_MM_HR)


def _segments(samples: list[RouteSample], status: str, threshold: float) -> list[BreachSegment]:
    idx = [i for i, s in enumerate(samples) if s.status == status]
    if not idx:
        return []
    groups: list[list[int]] = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= MERGE_GAP_SAMPLES + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    out = []
    for g in groups:
        members = [samples[i] for i in g]
        peak = max(members, key=lambda s: _severity(s.wind_mph or 0, s.precip_mm_hr or 0, threshold))
        reasons: list[str] = []
        for s in members:
            for r in s.reasons:
                kind = r.split(" ")[0]
                if not any(x.split(" ")[0] == kind for x in reasons):
                    reasons.append(r)
        # Report the peak sample's own reasons first: they carry the peak numbers.
        reasons = peak.reasons + [r for r in reasons if r.split(" ")[0] not in {p.split(" ")[0] for p in peak.reasons}]
        out.append(
            BreachSegment(
                kind="breach" if status == "breach" else "near",
                start_km=round(members[0].km, 1),
                end_km=round(members[-1].km, 1),
                start_eta=members[0].eta,
                end_eta=members[-1].eta,
                hazards=_hazard_kinds(reasons) or ["wind"],
                peak_wind_mph=round(max(s.wind_mph or 0 for s in members), 1),
                peak_precip_mm_hr=round(max(s.precip_mm_hr or 0 for s in members), 1),
                min_temp_f=round(min(s.temp_f if s.temp_f is not None else 999 for s in members), 1),
                peak_lat=peak.lat,
                peak_lon=peak.lon,
                near_place=nearest_place(peak.lat, peak.lon),
                from_place=nearest_place(members[0].lat, members[0].lon),
                to_place=nearest_place(members[-1].lat, members[-1].lon),
                reasons=reasons,
            )
        )
    return out


def assess_route(
    plan: RoutePlan,
    depart_at: datetime,
    cube: ForecastCube,
    cargo_description: Optional[str],
    *,
    step_km: float = SAMPLE_STEP_KM,
    window_h: float = ETA_WINDOW_H,
) -> RouteAssessment:
    points = decode(plan.polyline)
    path_km = path_length_km(points)
    pts, km = resample(points, step_km)
    etas = etas_along(km, path_km, depart_at, plan.duration_seconds, plan.legs)
    threshold = wind_threshold_for(cargo_description)
    spacing = float(km[1] - km[0]) if len(km) > 1 else 0.0

    samples: list[RouteSample] = []
    breached_cells: set[tuple[int, int]] = set()
    exposure_score = 0.0
    for (lat, lon), k, eta in zip(pts, km, etas):
        lat, lon = float(lat), float(lon)
        hours = cube.window(lat, lon, eta, window_h)
        if hours is None:
            samples.append(RouteSample(km=round(float(k), 2), lat=lat, lon=lon, eta=eta, status="unmonitored"))
            continue
        breach_reasons: list[str] = []
        worst_sev = -1.0
        for h in hours:
            breached, reasons = evaluate_breach(h.wind_mph, h.precip_mm_hr, h.temp_f, cargo_description)
            sev = _severity(h.wind_mph, h.precip_mm_hr, threshold)
            if breached and sev > worst_sev:
                breach_reasons, worst_sev = reasons, sev
        wind = max(h.wind_mph for h in hours)
        precip = max(h.precip_mm_hr for h in hours)
        temp = min(h.temp_f for h in hours)
        if breach_reasons:
            status, reasons = "breach", breach_reasons
            cell = cube.cell_index(lat, lon)
            if cell is not None:
                breached_cells.add(cell)
            exposure_score += spacing * max(1.0, _severity(wind, precip, threshold))
        else:
            reasons = near_threshold(wind, precip, temp, cargo_description)
            status = "near" if reasons else "clear"
        samples.append(
            RouteSample(
                km=round(float(k), 2), lat=lat, lon=lon, eta=eta,
                wind_mph=round(wind, 1), precip_mm_hr=round(precip, 2), temp_f=round(temp, 1),
                status=status, reasons=reasons,
            )
        )

    segments = _segments(samples, "breach", threshold)
    near_segments = _segments(samples, "near", threshold)
    monitored = [s for s in samples if s.status != "unmonitored"]
    unmonitored_fraction = 1 - len(monitored) / len(samples) if samples else 1.0
    peak = max(monitored, key=lambda s: s.wind_mph or 0, default=None)

    if segments:
        verdict = "hazardous"
    elif unmonitored_fraction > MAX_UNMONITORED_FRACTION:
        verdict = "unmonitored"
    elif near_segments:
        verdict = "at_risk"
    else:
        verdict = "clean"

    hazard_zones = None
    if breached_cells:
        boxes = [shapely.geometry.box(*cube.cell_bounds(yi, xi)) for yi, xi in breached_cells]
        hazard_zones = shapely.geometry.mapping(unary_union(boxes).buffer(0.01, join_style="mitre"))

    return RouteAssessment(
        route_id=plan.route_id,
        verdict=verdict,
        depart_at=depart_at,
        arrive_at=depart_at + timedelta(seconds=plan.duration_seconds),
        wind_threshold_mph=threshold,
        sample_spacing_km=round(spacing, 2),
        samples=samples,
        segments=segments,
        near_segments=near_segments,
        peak_wind_mph=peak.wind_mph if peak else None,
        peak_wind_km=peak.km if peak else None,
        unmonitored_fraction=round(unmonitored_fraction, 4),
        exposure_km=round(sum(spacing for s in samples if s.status == "breach"), 1),
        exposure_score=round(exposure_score, 2),
        hazard_zones=hazard_zones,
    )
