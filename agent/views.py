"""Pipeline results -> event views (agent/events.py). Display strings come from the FactSheet so
the map labels, the trace and the advisory can't disagree."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from agent.events import (
    AssessmentView,
    CandidateView,
    DelayOptionView,
    PlaceView,
    Provenance,
    RouteView,
    SampleView,
    SegmentView,
    ShipmentView,
)
from schemas import BreachSegment, DelayAnalysis, DetourAnalysis, RouteAssessment, RoutePlan
from tools.cube import CubeMeta
from tools.facts import (
    FactSheet,
    fmt_distance,
    fmt_duration,
    fmt_wind,
    fmt_window,
    shipment_payload,
)
from tools.geo import _cumulative_km, etas_along
from tools.polyline_codec import decode
from tools.thresholds import wind_threshold_for

MAX_SAMPLES = 400


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if t else None


def _t_s(t: datetime, depart: datetime) -> int:
    return int(round((t - depart).total_seconds()))


def shipment_view(fs: FactSheet) -> ShipmentView:
    s = fs.shipment
    p = shipment_payload(fs)

    def place(w) -> PlaceView:
        return PlaceView(name=w.name, lat=w.location.lat, lon=w.location.lon)

    return ShipmentView(
        shipment_id=s.shipment_id, lane=p["lane"], carrier=s.carrier, cargo=s.cargo_description,
        wind_threshold=p["wind_threshold"], wind_threshold_mph=wind_threshold_for(s.cargo_description),
        threshold_basis=p["threshold_basis"], depart=p["depart"], deadline=p["deadline"], timezone=s.timezone,
        depart_iso=_iso(s.origin.eta), deadline_iso=_iso(s.delivery_deadline),
        origin=place(s.origin), destination=place(s.destination), stops=[place(w) for w in s.waypoints],
    )


def provenance(meta: CubeMeta) -> Provenance:
    return Provenance(
        source=meta.source, init_time=meta.init_time.isoformat().replace("+00:00", "Z"), label=meta.label,
        dataset=meta.dataset,
    )


def route_view(fs: FactSheet, plan: RoutePlan, depart: datetime, tolerance_deg: float = 0.0015) -> RouteView:
    """Simplified [lon, lat] path plus seconds-after-departure at each kept vertex, timed by distance
    along the ORIGINAL polyline so the truck icon moves exactly as assess_route assumed."""
    from shapely.geometry import LineString

    pts = decode(plan.polyline)
    arr = np.asarray(pts, dtype=float)
    cum = _cumulative_km(arr) if len(arr) > 1 else np.zeros(len(arr))
    if len(pts) >= 3:
        simp = LineString([(lon, lat) for lat, lon in pts]).simplify(tolerance_deg, preserve_topology=False)
        kept = [(y, x) for x, y in simp.coords]
        # simplify() keeps original vertices, so each maps back to its index exactly (in order).
        idx, j = [], 0
        for la, lo in kept:
            while j < len(arr) - 1 and not (arr[j, 0] == la and arr[j, 1] == lo):
                j += 1
            idx.append(j)
    else:
        kept, idx = list(pts), list(range(len(pts)))
    etas = etas_along(cum[idx], float(cum[-1]) if len(cum) else 0.0, depart, plan.duration_seconds, plan.legs)
    return RouteView(
        id=plan.route_id, description=plan.description, source=plan.source, via=plan.via_label,
        distance=fmt_distance(plan.distance_meters), duration=fmt_duration(plan.duration_seconds),
        arrive=fs.time(depart + timedelta(seconds=plan.duration_seconds)),
        distance_km=round(plan.distance_meters / 1000, 1),
        path=[[round(lo, 5), round(la, 5)] for la, lo in kept], path_t_s=[_t_s(e, depart) for e in etas],
    )


def _segment_view(fs: FactSheet, seg: BreachSegment, spacing_km: float, threshold: float, depart: datetime) -> SegmentView:
    stretch = seg.from_place if seg.from_place == seg.to_place else f"{seg.from_place} → {seg.to_place}"
    return SegmentView(
        kind=seg.kind, start_km=seg.start_km, end_km=seg.end_km,
        start_t_s=_t_s(seg.start_eta, depart), end_t_s=_t_s(seg.end_eta, depart),
        stretch=stretch, place=seg.near_place, window=fmt_window(seg.start_eta, seg.end_eta, fs.tz, fs.ref),
        length=fmt_distance(max(seg.end_km - seg.start_km, spacing_km) * 1000),
        peak_wind=fmt_wind(seg.peak_wind_mph, threshold), hazards=list(seg.hazards),
        peak_lat=seg.peak_lat, peak_lon=seg.peak_lon,
    )


def assessment_view(fs: FactSheet, a: RouteAssessment) -> AssessmentView:
    thr = a.wind_threshold_mph
    return AssessmentView(
        route_id=a.route_id, verdict=a.verdict, threshold_mph=thr,
        peak_wind=fmt_wind(a.peak_wind_mph, thr) if a.peak_wind_mph is not None else None,
        exposure=fmt_distance(a.exposure_km * 1000) if a.segments else None,
        unmonitored_fraction=a.unmonitored_fraction,
        segments=[_segment_view(fs, s, a.sample_spacing_km, thr, a.depart_at) for s in a.segments],
        near_segments=[_segment_view(fs, s, a.sample_spacing_km, thr, a.depart_at) for s in a.near_segments],
    )


def samples_view(a: RouteAssessment) -> list[SampleView]:
    step = max(1, int(np.ceil(len(a.samples) / MAX_SAMPLES)))
    return [
        SampleView(km=s.km, lon=round(s.lon, 4), lat=round(s.lat, 4), t_s=_t_s(s.eta, a.depart_at),
                   wind=s.wind_mph, precip=s.precip_mm_hr, status=s.status)
        for s in a.samples[::step]
    ]


def candidate_views(fs: FactSheet, d: DetourAnalysis) -> list[CandidateView]:
    out = []
    for c in d.candidates:
        key = f"option.{c.option_id}"
        label = c.plan.via_label or c.plan.description
        out.append(CandidateView(
            option_id=c.option_id, label=label,
            route=route_view(fs, c.plan, c.assessment.depart_at),
            assessment=assessment_view(fs, c.assessment),
            delta_distance=fs.get(f"{key}.delta_distance") or "",
            delta_duration=fs.get(f"{key}.delta_duration") or "",
            cost=fs.get(f"{key}.cost") or "",
            meets_deadline=(c.assessment.arrive_at <= fs.shipment.delivery_deadline) if fs.shipment.delivery_deadline else None,
        ))
    return out


def delay_views(fs: FactSheet, dl: DelayAnalysis) -> list[DelayOptionView]:
    thr = wind_threshold_for(fs.shipment.cargo_description)
    return [
        DelayOptionView(
            option_id=o.option_id, shift=f"+{o.shift_hours} h", shift_hours=o.shift_hours,
            depart=fs.time(o.depart_at), arrive=fs.time(o.arrive_at), verdict=o.verdict,
            peak_wind=fmt_wind(o.peak_wind_mph, thr) if o.peak_wind_mph is not None else None,
            meets_deadline=o.meets_deadline,
        )
        for o in dl.options
    ]
