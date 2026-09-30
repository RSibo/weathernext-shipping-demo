"""Deterministic safety-threshold evaluation. No I/O, no LLM, no randomness — this is the single
file in the repo that never needs a mock to test.

Thresholds are demo-calibrated (wind ~ NWS high-wind-warning territory), not a certified dispatch
safety standard — disclosed here rather than presented as authoritative.
"""

from typing import Optional

DRY_VAN_WIND_THRESHOLD_MPH = 35.0
WIND_THRESHOLD_MPH = 45.0
PRECIP_THRESHOLD_MM_HR = 25.0
BLACK_ICE_TEMP_F = 32.0
# "Measurable" precipitation for the black-ice rule. WeatherNext 3's p90 IMERG precip carries
# trace values (~0.03 mm/hr) almost everywhere, so a bare `> 0` flagged nearly every sub-freezing
# cell in the Mountain West as black ice. 0.1 mm/hr is the conventional measurable-precip floor.
BLACK_ICE_MIN_PRECIP_MM_HR = 0.1
# Fraction of a threshold at which a reading is "near" it: not a breach, but worth monitoring.
NEAR_THRESHOLD_FRACTION = 0.85


def is_high_profile(cargo_description: Optional[str]) -> bool:
    """Empty or dry-van trailers: high-profile, light, the ones WYDOT closes I-80 to first."""
    return bool(
        cargo_description and any(kw in cargo_description.lower() for kw in ("dry van", "empty"))
    )


def wind_threshold_for(cargo_description: Optional[str]) -> float:
    return DRY_VAN_WIND_THRESHOLD_MPH if is_high_profile(cargo_description) else WIND_THRESHOLD_MPH


def evaluate_breach(
    wind_speed_mph: float,
    precip_mm_hr: float,
    temp_f: float,
    cargo_description: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Evaluate operational safety thresholds with cargo awareness.

    Black ice uses precip_mm_hr >= BLACK_ICE_MIN_PRECIP_MM_HR, NOT >= PRECIP_THRESHOLD_MM_HR — any
    measurable precipitation at or below freezing is a black-ice risk, a materially lower bar than
    the heavy-precipitation threshold. Do not "simplify" this to share the same precip comparison as the storm check.

    Wind thresholds adapt based on trailer dynamics: empty or dry-van trailers have higher rollover
    risk in crosswinds (alerting at 35 mph), whereas heavy loaded flatbeds sustain up to 45 mph.
    """
    reasons: list[str] = []

    is_dry_van = is_high_profile(cargo_description)
    wind_threshold = wind_threshold_for(cargo_description)

    if wind_speed_mph >= wind_threshold:
        trailer_note = " (dry-van rollover risk)" if is_dry_van else ""
        reasons.append(
            f"Wind {wind_speed_mph:.1f}mph exceeds {wind_threshold:.0f}mph threshold{trailer_note}"
        )

    if precip_mm_hr >= PRECIP_THRESHOLD_MM_HR:
        reasons.append(
            f"Precipitation {precip_mm_hr:.1f}mm/hr exceeds {PRECIP_THRESHOLD_MM_HR:.0f}mm/hr threshold"
        )

    if temp_f <= BLACK_ICE_TEMP_F and precip_mm_hr >= BLACK_ICE_MIN_PRECIP_MM_HR:
        reasons.append(f"Black ice risk: {temp_f:.1f}°F with {precip_mm_hr:.1f}mm/hr precipitation")

    return bool(reasons), reasons


def near_threshold(
    wind_speed_mph: float,
    precip_mm_hr: float,
    temp_f: float,
    cargo_description: Optional[str] = None,
) -> list[str]:
    """Reasons a reading that did NOT breach is still within NEAR_THRESHOLD_FRACTION of a limit.
    Callers must check evaluate_breach first; this says nothing about readings that breach."""
    reasons: list[str] = []
    wind_threshold = wind_threshold_for(cargo_description)
    if NEAR_THRESHOLD_FRACTION * wind_threshold <= wind_speed_mph < wind_threshold:
        reasons.append(f"Wind {wind_speed_mph:.1f}mph within 15% of {wind_threshold:.0f}mph threshold")
    if NEAR_THRESHOLD_FRACTION * PRECIP_THRESHOLD_MM_HR <= precip_mm_hr < PRECIP_THRESHOLD_MM_HR:
        reasons.append(
            f"Precipitation {precip_mm_hr:.1f}mm/hr within 15% of {PRECIP_THRESHOLD_MM_HR:.0f}mm/hr threshold"
        )
    return reasons
