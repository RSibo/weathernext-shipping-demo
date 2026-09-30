"""FactSheet: every number a person or Gemini sees, formatted exactly once.

Tool results sent to Gemini carry these display strings instead of raw floats, the UI renders the
same strings, and the grounding check (agent/advisory.py) only accepts numbers in advisory prose
that appear here. So "+14 min" on the KPI tile, in the tool result and in the driver message is
one value from one formatter — Gemini never has to convert 840 seconds, and can't invent 52 mph.

Times are lane-local (the dispatcher's clock), distances are miles with km alongside.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import config
from schemas import (
    BreachSegment,
    DelayAnalysis,
    DetourAnalysis,
    RouteAssessment,
    RoutePlan,
    Shipment,
)
from tools.thresholds import is_high_profile, wind_threshold_for

M_PER_MI = 1609.344
MINUS = "−"
WIND_METRIC = "p90 sustained 10 m wind (WeatherNext 3 ensemble: 9 in 10 members at or below this value)"


# --- Formatters -----------------------------------------------------------------------------
def _signed(value: float, text: str) -> str:
    return f"{'+' if value >= 0 else MINUS}{text}"


def fmt_distance(meters: float) -> str:
    return f"{meters / M_PER_MI:,.0f} mi ({meters / 1000:,.0f} km)"


def fmt_delta_distance(meters: float) -> str:
    mi, km = abs(meters) / M_PER_MI, abs(meters) / 1000
    return f"{_signed(meters, f'{mi:,.0f} mi')} ({_signed(meters, f'{km:,.0f} km')})"


def fmt_duration(seconds: float) -> str:
    h, m = divmod(int(round(abs(seconds) / 60)), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def fmt_delta_duration(seconds: float) -> str:
    return _signed(seconds, fmt_duration(seconds))


def fmt_time(dt: datetime, tz: ZoneInfo, ref: Optional[datetime] = None, *, weekday: bool = False) -> str:
    """'17:55 MDT'; the weekday is added when asked for or when the day differs from ref."""
    local = dt.astimezone(tz)
    other_day = ref is not None and local.date() != ref.astimezone(tz).date()
    return f"{local:%a %H:%M} {local.tzname()}" if weekday or other_day else f"{local:%H:%M} {local.tzname()}"


def fmt_window(start: datetime, end: datetime, tz: ZoneInfo, ref: Optional[datetime] = None) -> str:
    a, b = start.astimezone(tz), end.astimezone(tz)
    ref_day = (ref or start).astimezone(tz).date()
    if f"{a:%a %H:%M}" == f"{b:%a %H:%M}":  # a single sample: "12:09 MDT", not "12:09–12:09 MDT"
        return fmt_time(a, tz, ref)
    if a.date() == b.date() == ref_day:
        return f"{a:%H:%M}–{b:%H:%M} {b.tzname()}"
    return f"{a:%a %H:%M}–{b:%a %H:%M} {b.tzname()}"


def fmt_wind(mph: float, threshold: Optional[float] = None) -> str:
    # Never let rounding make a below-threshold value read as the threshold itself.
    if threshold is not None and mph < threshold and round(mph) >= threshold:
        return f"{mph:.1f} mph"
    return f"{mph:.0f} mph"


def fmt_precip(mm_hr: float) -> str:
    return f"{mm_hr:.1f} mm/hr"


def fmt_temp(f: float) -> str:
    return f"{f:.0f}°F"


def fmt_cost(delta_meters: float) -> str:
    mi = delta_meters / M_PER_MI
    if mi < 0.5:
        return "no added mileage cost"
    return f"${mi * config.DETOUR_COST_PER_MILE_USD:,.0f} est."


# --- FactSheet --------------------------------------------------------------------------------
class FactSheet:
    """Display strings for one run, keyed so the UI can find them ('baseline.distance', ...)."""

    def __init__(self, shipment: Shipment):
        self.shipment = shipment
        self.tz = ZoneInfo(shipment.timezone)
        self.ref = shipment.origin.eta
        self._facts: dict[str, str] = {}

    def put(self, key: str, value: str) -> str:
        self._facts[key] = value
        return value

    def get(self, key: str) -> Optional[str]:
        return self._facts.get(key)

    def as_dict(self) -> dict[str, str]:
        return dict(self._facts)

    def strings(self) -> list[str]:
        return list(self._facts.values())

    def time(self, dt: datetime, *, weekday: bool = False) -> str:
        return fmt_time(dt, self.tz, self.ref, weekday=weekday)


# --- Payload builders (what Gemini and the UI receive) ------------------------------------------
def shipment_payload(fs: FactSheet) -> dict:
    s = fs.shipment
    thr = wind_threshold_for(s.cargo_description)
    return {
        "shipment_id": s.shipment_id,
        "lane": fs.put("lane", f"{s.origin.name} → {s.destination.name}"),
        "stops": [w.name for w in s.waypoints],
        "carrier": s.carrier,
        "cargo": s.cargo_description,
        "wind_threshold": fs.put("threshold.wind", f"{thr:.0f} mph"),
        "threshold_basis": (
            "high-profile trailer (empty or dry van) — blow-over risk" if is_high_profile(s.cargo_description)
            else "standard loaded trailer"
        ),
        "depart": fs.put("depart", fs.time(s.origin.eta, weekday=True)),
        "deadline": fs.put("deadline", fs.time(s.delivery_deadline, weekday=True)) if s.delivery_deadline else None,
    }


def segment_payload(fs: FactSheet, key: str, seg: BreachSegment, spacing_km: float, threshold: float) -> dict:
    stretch = seg.from_place if seg.from_place == seg.to_place else f"{seg.from_place} → {seg.to_place}"
    length_m = max(seg.end_km - seg.start_km, spacing_km) * 1000
    out = {
        "stretch": fs.put(f"{key}.stretch", stretch),
        "peak_near": fs.put(f"{key}.place", seg.near_place),
        "window": fs.put(f"{key}.window", fmt_window(seg.start_eta, seg.end_eta, fs.tz, fs.ref)),
        "length": fs.put(f"{key}.length", fmt_distance(length_m)),
        "peak_wind": fs.put(f"{key}.peak_wind", fmt_wind(seg.peak_wind_mph, threshold)),
        "hazards": seg.hazards,
    }
    if "precip" in seg.hazards or "black_ice" in seg.hazards:
        out["peak_precip"] = fs.put(f"{key}.peak_precip", fmt_precip(seg.peak_precip_mm_hr))
    if "black_ice" in seg.hazards:
        out["min_temp"] = fs.put(f"{key}.min_temp", fmt_temp(seg.min_temp_f))
    return out


def route_payload(fs: FactSheet, key: str, plan: RoutePlan, a: RouteAssessment) -> dict:
    thr = a.wind_threshold_mph
    out = {
        "route": fs.put(f"{key}.route", plan.description),
        "distance": fs.put(f"{key}.distance", fmt_distance(plan.distance_meters)),
        "drive_time": fs.put(f"{key}.duration", fmt_duration(plan.duration_seconds)),
        "arrive": fs.put(f"{key}.arrive", fs.time(a.arrive_at)),
        "verdict": a.verdict,
        "peak_wind": fs.put(f"{key}.peak_wind", fmt_wind(a.peak_wind_mph, thr)) if a.peak_wind_mph is not None else None,
        "breaches": [segment_payload(fs, f"{key}.breach.{i}", s, a.sample_spacing_km, thr) for i, s in enumerate(a.segments)],
    }
    if a.segments:
        out["exposure"] = fs.put(f"{key}.exposure", fmt_distance(a.exposure_km * 1000))
    if a.near_segments:
        worst = sorted(a.near_segments, key=lambda s: -s.peak_wind_mph)[:2]
        out["near_threshold"] = [segment_payload(fs, f"{key}.near.{i}", s, a.sample_spacing_km, thr) for i, s in enumerate(worst)]
    if a.unmonitored_fraction > 0:
        out["unmonitored"] = fs.put(f"{key}.unmonitored", f"{a.unmonitored_fraction:.0%} of the route has no forecast coverage")
    if fs.shipment.delivery_deadline:
        out["meets_deadline"] = a.arrive_at <= fs.shipment.delivery_deadline
    return out


def weather_payload(fs: FactSheet, plan: RoutePlan, a: RouteAssessment, forecast_label: str) -> dict:
    return {
        **shipment_payload(fs),
        "forecast": forecast_label,
        "wind_metric": WIND_METRIC,
        "method": fs.put(
            "method", f"{len(a.samples)} points every {a.sample_spacing_km:.0f} km, each read at its own ETA ±1 h"
        ),
        **route_payload(fs, "baseline", plan, a),
    }


def detour_payload(fs: FactSheet, d: DetourAnalysis) -> dict:
    cands = []
    for c in d.candidates:
        key = f"option.{c.option_id}"
        p = route_payload(fs, key, c.plan, c.assessment)
        p = {
            "option_id": c.option_id,
            "via": c.plan.via_label,
            **p,
            "extra_distance": fs.put(f"{key}.delta_distance", fmt_delta_distance(c.delta_distance_meters)),
            "extra_time": fs.put(f"{key}.delta_duration", fmt_delta_duration(c.delta_duration_seconds)),
            "extra_cost": fs.put(f"{key}.cost", fmt_cost(c.delta_distance_meters)),
        }
        cands.append({k: v for k, v in p.items() if v is not None})
    note = (
        "chosen option is the fastest candidate below every threshold"
        if not d.still_hazardous
        else "no candidate avoids the hazard; chosen option (if any) only reduces exposure and must NOT be presented as safe"
    )
    return {"candidates": cands, "chosen_option_id": d.chosen_id, "still_hazardous": d.still_hazardous, "note": note}


def delay_payload(fs: FactSheet, dl: DelayAnalysis) -> dict:
    opts = []
    for o in dl.options:
        key = f"option.{o.option_id}"
        item = {
            "option_id": o.option_id,
            "shift": fs.put(f"{key}.shift", f"+{o.shift_hours} h"),
            "depart": fs.put(f"{key}.depart", fs.time(o.depart_at)),
            "arrive": fs.put(f"{key}.arrive", fs.time(o.arrive_at)),
            "verdict": o.verdict,
        }
        if o.peak_wind_mph is not None:
            item["peak_wind"] = fs.put(f"{key}.peak_wind", fmt_wind(o.peak_wind_mph, wind_threshold_for(fs.shipment.cargo_description)))
        if o.meets_deadline is not None:
            item["meets_deadline"] = o.meets_deadline
        opts.append(item)
    return {"options": opts, "earliest_safe_option_id": dl.earliest_safe_id}
