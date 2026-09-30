"""Advisory guardrails: what Gemini may decide, and whether what it wrote is grounded.

Three checks run on every submit_advisory call, in Python, before anything reaches the screen:

1. Decision: the status and chosen_option_id must be allowed by what the tools found (never CLEAR
   a breach, never REROUTE onto a hazardous route, never pick an option that misses the deadline
   when a safe one meets it).
2. Grounding: every number-with-unit, clock time and road name in the prose must appear in the
   run's FactSheet (or in the fixed policy thresholds). Gemini can't invent "52 mph" or "I-25".
3. Form: length limits, no option ids in prose, winds are never "gusts".

A rejected advisory goes back to Gemini once with the issues listed; after that the run uses
fallback_advisory(), a deterministic template built from the same FactSheet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent.events import Metric
from schemas import (
    DelayAnalysis,
    DetourAnalysis,
    DetourOption,
    RouteAssessment,
    RoutePlan,
    Shipment,
)
from tools.facts import FactSheet
from tools.thresholds import (
    BLACK_ICE_MIN_PRECIP_MM_HR,
    BLACK_ICE_TEMP_F,
    DRY_VAN_WIND_THRESHOLD_MPH,
    PRECIP_THRESHOLD_MM_HR,
    WIND_THRESHOLD_MPH,
)

Status = Literal["CLEAR", "MONITOR", "REROUTE", "HOLD"]
HEADLINE_MAX = 90
DRIVER_MAX = 300
SAFE = ("clean", "at_risk")

# Numbers a human may legitimately cite that are policy, not forecast output.
POLICY_STRINGS = (
    f"{DRY_VAN_WIND_THRESHOLD_MPH:.0f} mph",
    f"{WIND_THRESHOLD_MPH:.0f} mph",
    f"{PRECIP_THRESHOLD_MM_HR:.0f} mm/hr",
    f"{BLACK_ICE_TEMP_F:.0f}°F",
    f"{BLACK_ICE_MIN_PRECIP_MM_HR:.1f} mm/hr",
    "10 m",
)


class AdvisorySubmission(BaseModel):
    status: Status
    chosen_option_id: str
    headline: str = Field(..., min_length=1)
    recommendation: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    driver_message: str = Field(..., min_length=1)

    def prose(self) -> str:
        return "\n".join((self.headline, self.recommendation, self.rationale, self.driver_message))


@dataclass
class Findings:
    """What the tools have established so far in one run."""

    shipment: Shipment
    baseline: Optional[RoutePlan] = None
    assessment: Optional[RouteAssessment] = None
    detour: Optional[DetourAnalysis] = None
    delay: Optional[DelayAnalysis] = None
    tools_called: list[str] = field(default_factory=list)


@dataclass
class Validation:
    ok: bool
    issues: list[str]
    grounded_numbers: int = 0


# --- 1. Decision ----------------------------------------------------------------------------------
def _detour_meets_deadline(f: Findings, c: DetourOption) -> Optional[bool]:
    dl = f.shipment.delivery_deadline
    return None if dl is None else c.assessment.arrive_at <= dl


def allowed_options(f: Findings) -> dict[str, list[str]]:
    """status -> option ids the evidence supports, best first. Empty dict = not enough evidence yet."""
    a = f.assessment
    if a is None:
        return {}
    if a.verdict == "clean":
        return {"CLEAR": ["baseline"]}
    if a.verdict in ("at_risk", "unmonitored"):
        return {"MONITOR": ["baseline"]}
    if f.detour is None or f.delay is None:
        return {}

    detours = sorted(
        (c for c in f.detour.candidates if c.assessment.verdict in SAFE),
        key=lambda c: (c.assessment.verdict != "clean", c.plan.duration_seconds),
    )
    delays = [o for o in f.delay.options if o.verdict in SAFE]
    on_time = [c for c in detours if _detour_meets_deadline(f, c) is not False] + [
        o for o in delays if o.meets_deadline is not False
    ]
    if on_time:  # something safe arrives in time: late options are off the table
        detours = [c for c in detours if _detour_meets_deadline(f, c) is not False]
        delays = [o for o in delays if o.meets_deadline is not False]

    out: dict[str, list[str]] = {}
    if detours:
        out["REROUTE"] = [c.option_id for c in detours]
    if delays:
        out["HOLD"] = [o.option_id for o in delays]
    if not out:
        out["HOLD"] = ["hold"]  # nothing safe inside the forecast: hold and re-check next init
    return out


def best_option(f: Findings) -> tuple[Status, str]:
    """The option a careful dispatcher would pick: earliest arrival among the allowed ones."""
    allowed = allowed_options(f)
    if not allowed:
        raise ValueError("not enough evidence to advise")
    if "REROUTE" in allowed and "HOLD" in allowed and allowed["HOLD"] != ["hold"]:
        detour = next(c for c in f.detour.candidates if c.option_id == allowed["REROUTE"][0])
        delay = next(o for o in f.delay.options if o.option_id == allowed["HOLD"][0])
        return ("REROUTE", detour.option_id) if detour.assessment.arrive_at <= delay.arrive_at else ("HOLD", delay.option_id)
    status = "REROUTE" if "REROUTE" in allowed else next(iter(allowed))
    return status, allowed[status][0]


def check_decision(f: Findings, sub: AdvisorySubmission) -> list[str]:
    allowed = allowed_options(f)
    if not allowed:
        if f.assessment is None:
            return ["call check_route_weather before submit_advisory"]
        return ["the route is hazardous: call compute_detour_route and evaluate_departure_delay before submit_advisory"]
    if sub.status not in allowed:
        opts = "; ".join(f"{s} with {', '.join(ids)}" for s, ids in allowed.items())
        return [f"status {sub.status} is not supported by the tool results; allowed: {opts}"]
    if sub.chosen_option_id not in allowed[sub.status]:
        return [
            f"chosen_option_id '{sub.chosen_option_id}' is not allowed for {sub.status}; "
            f"use one of: {', '.join(allowed[sub.status])}"
        ]
    return []


# --- 2. Grounding ---------------------------------------------------------------------------------
_UNIT = {
    "mph": "mph", "mm/hr": "mm/hr", "mm/h": "mm/hr", "°f": "°F", "°": "°F", "f": "°F",
    "km": "km", "mi": "mi", "mile": "mi", "miles": "mi",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h", "%": "%",
}
# A number that isn't part of a road name ("I-80") or an identifier ("p90", "SHP-2001").
_NUM = r"(?<![A-Za-z]-)(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
_QTY_RE = re.compile(
    _NUM + r"\s?(mph|mm/hr|mm/h|°F|°|km|miles|mile|mi|minutes|minute|mins|min|hours|hour|hrs|hr|h|%)(?![A-Za-z/])",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")
_TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_ROAD_RE = re.compile(r"\b(I|US|CO|WY|UT|NV|AZ|CA|TX|OK|NM|ID|SR|Hwy|Highway|Route)[- ](\d{1,3})\b")
_OPTION_ID_RE = re.compile(r"\b(?:detour|delay|alt|via):[\w+\-.]+")


def _num(whole: str, frac: Optional[str]) -> str:
    n = float(whole.replace(",", "") + (f".{frac}" if frac else ""))
    return f"{n:g}"


def extract_tokens(text: str) -> set[str]:
    """Canonical tokens for every quantity, price, clock time and road number in text."""
    toks: set[str] = set()
    for m in _QTY_RE.finditer(text):
        toks.add(f"{_num(m.group(1), m.group(2))} {_UNIT[m.group(3).lower()]}")
    for m in _MONEY_RE.finditer(text):
        toks.add(f"${_num(m.group(1), m.group(2))}")
    for m in _TIME_RE.finditer(text):
        toks.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    for m in _ROAD_RE.finditer(text):
        prefix = m.group(1).upper()
        toks.add(f"road {prefix if prefix in ('I', 'US') else 'STATE'}-{m.group(2)}")
    return toks


def allowed_tokens(fs: FactSheet) -> set[str]:
    toks: set[str] = set()
    for s in (*fs.strings(), *POLICY_STRINGS):
        toks |= extract_tokens(s)
    return toks


def check_grounding(fs: FactSheet, sub: AdvisorySubmission) -> tuple[list[str], int]:
    used = extract_tokens(sub.prose())
    unknown = sorted(used - allowed_tokens(fs))
    issues = []
    if unknown:
        shown = ", ".join(t.removeprefix("road ") for t in unknown)
        issues.append(
            f"not found in any tool result: {shown}. Copy numbers, times and road names exactly as the tools "
            "returned them; do not compute, convert or round."
        )
    return issues, len(used) - len(unknown)


# --- 3. Form --------------------------------------------------------------------------------------
def check_form(sub: AdvisorySubmission) -> list[str]:
    issues = []
    if len(sub.headline) > HEADLINE_MAX:
        issues.append(f"headline is {len(sub.headline)} characters; the limit is {HEADLINE_MAX}")
    if len(sub.driver_message) > DRIVER_MAX:
        issues.append(f"driver_message is {len(sub.driver_message)} characters; the limit is {DRIVER_MAX}")
    if _OPTION_ID_RE.search(sub.prose()):
        issues.append("use route names or departure times in prose, not option ids like 'detour:…' or 'delay:…'")
    if re.search(r"\bgusts?\b", sub.prose(), re.IGNORECASE):
        issues.append("the forecast is p90 sustained wind, not gusts: do not say 'gust'")
    return issues


def validate(f: Findings, fs: FactSheet, sub: AdvisorySubmission) -> Validation:
    issues = check_decision(f, sub)
    grounding, grounded = check_grounding(fs, sub)
    issues += grounding + check_form(sub)
    return Validation(ok=not issues, issues=issues, grounded_numbers=grounded)


# --- Fallback and metrics -------------------------------------------------------------------------
def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,;·—") + "…"


def fallback_advisory(f: Findings, fs: FactSheet) -> AdvisorySubmission:
    """Deterministic advisory from the FactSheet. Plain, correct, and grounded by construction."""
    status, option = best_option(f)
    a = f.assessment
    thr = fs.get("threshold.wind")
    peak = fs.get("baseline.peak_wind") or "n/a"
    route = fs.get("baseline.route")
    # Cite the worst stretch (it carries the route's peak), not merely the first one.
    worst = max(range(len(a.segments)), key=lambda i: a.segments[i].peak_wind_mph) if a.segments else None
    b = f"baseline.breach.{worst}"
    where = (
        f"p90 sustained winds reach {fs.get(b + '.peak_wind')} near {fs.get(b + '.place')}, "
        f"{fs.get(b + '.window')}, above the {thr} limit for this trailer"
        if worst is not None
        else ""
    )
    if status == "CLEAR":
        return AdvisorySubmission(
            status=status, chosen_option_id=option,
            headline=_clip(f"Clear to run {route}: peak {peak}, under the {thr} limit", HEADLINE_MAX),
            recommendation=f"Run as planned on {route}; arrive {fs.get('baseline.arrive')}.",
            rationale=f"Peak p90 sustained wind along the route is {peak} against a {thr} limit. {fs.get('method')}.",
            driver_message=_clip(f"You're clear to run {route} as planned. Expected arrival {fs.get('baseline.arrive')}.", DRIVER_MAX),
        )
    if status == "MONITOR":
        if a.verdict == "unmonitored":
            why = fs.get("baseline.unmonitored") or "part of the route has no forecast coverage"
        else:
            why = (
                f"p90 sustained winds reach {fs.get('baseline.near.0.peak_wind')} near {fs.get('baseline.near.0.place')}, "
                f"{fs.get('baseline.near.0.window')}, close to the {thr} limit"
            )
        return AdvisorySubmission(
            status=status, chosen_option_id=option,
            headline=_clip(f"Monitor {route}: peak {peak} against a {thr} limit", HEADLINE_MAX),
            recommendation=f"Run as planned on {route} and re-check before departure; arrive {fs.get('baseline.arrive')}.",
            rationale=f"No threshold is exceeded, but {why}.",
            driver_message=_clip(f"Run {route} as planned. Winds are strong but under your limit; stay alert and check in before departure.", DRIVER_MAX),
        )
    if status == "REROUTE":
        k = f"option.{option}"
        new_route = fs.get(f"{k}.route")
        return AdvisorySubmission(
            status=status, chosen_option_id=option,
            headline=_clip(f"Reroute via {new_route}: {peak} winds on {route}", HEADLINE_MAX),
            recommendation=(
                f"Take {new_route} instead of {route}: {fs.get(f'{k}.delta_duration')}, {fs.get(f'{k}.delta_distance')}, "
                f"{fs.get(f'{k}.cost')}; arrive {fs.get(f'{k}.arrive')}."
            ),
            rationale=f"On {route}, {where}. {new_route} stays below every threshold at its own arrival times.",
            driver_message=_clip(
                f"Change of route: take {new_route} instead of {route}. High winds on {route} near "
                f"{fs.get(b + '.place')}. New arrival {fs.get(f'{k}.arrive')}.",
                DRIVER_MAX,
            ),
        )
    # HOLD
    if option == "hold":
        return AdvisorySubmission(
            status=status, chosen_option_id=option,
            headline=_clip(f"Hold: {peak} winds on {route}, no safe option in the forecast", HEADLINE_MAX),
            recommendation="Hold the load and re-check when the next WeatherNext 3 forecast is issued.",
            rationale=f"On {route}, {where}. No detour or later departure in the forecast stays below the threshold.",
            driver_message=_clip("Hold at the yard. High winds on your route; dispatch will re-check with the next forecast.", DRIVER_MAX),
        )
    k = f"option.{option}"
    return AdvisorySubmission(
        status=status, chosen_option_id=option,
        headline=_clip(f"Hold until {fs.get(f'{k}.depart')}: {peak} winds on {route}", HEADLINE_MAX),
        recommendation=f"Delay departure {fs.get(f'{k}.shift')} to {fs.get(f'{k}.depart')}; arrive {fs.get(f'{k}.arrive')}.",
        rationale=f"On {route}, {where}. Departing {fs.get(f'{k}.depart')} keeps every point below the threshold.",
        driver_message=_clip(
            f"Hold departure until {fs.get(f'{k}.depart')}. High winds on {route}. New arrival {fs.get(f'{k}.arrive')}.",
            DRIVER_MAX,
        ),
    )


def metrics_for(f: Findings, fs: FactSheet, status: Status, option: str) -> list[Metric]:
    """KPI tiles for the chosen option, all FactSheet strings."""
    if status == "REROUTE":
        k = f"option.{option}"
        return [
            Metric(key="delta_duration", label="Extra time", value=fs.get(f"{k}.delta_duration") or "—"),
            Metric(key="delta_distance", label="Extra distance", value=fs.get(f"{k}.delta_distance") or "—"),
            Metric(key="cost", label="Detour cost", value=fs.get(f"{k}.cost") or "—"),
            Metric(key="arrive", label="New arrival", value=fs.get(f"{k}.arrive") or "—",
                   tone="good" if _detour_meets_deadline(f, _cand(f, option)) is not False else "warn"),
        ]
    if status == "HOLD" and option != "hold":
        k = f"option.{option}"
        meets = next((o.meets_deadline for o in f.delay.options if o.option_id == option), None)
        return [
            Metric(key="shift", label="Delay", value=fs.get(f"{k}.shift") or "—"),
            Metric(key="depart", label="New departure", value=fs.get(f"{k}.depart") or "—"),
            Metric(key="arrive", label="New arrival", value=fs.get(f"{k}.arrive") or "—",
                   tone="bad" if meets is False else "good" if meets else "neutral"),
            Metric(key="peak", label="Peak wind", value=fs.get(f"{k}.peak_wind") or "—"),
        ]
    tone = {"clean": "good", "at_risk": "warn", "hazardous": "bad"}.get(f.assessment.verdict, "warn")
    return [
        Metric(key="peak", label="Peak wind", value=fs.get("baseline.peak_wind") or "—", tone=tone),
        Metric(key="threshold", label="Limit", value=fs.get("threshold.wind") or "—"),
        Metric(key="arrive", label="Arrival", value=fs.get("baseline.arrive") or "—"),
        Metric(key="distance", label="Distance", value=fs.get("baseline.distance") or "—"),
    ]


def _cand(f: Findings, option: str) -> DetourOption:
    return next(c for c in f.detour.candidates if c.option_id == option)
