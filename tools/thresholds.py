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


def evaluate_breach(
    wind_speed_mph: float,
    precip_mm_hr: float,
    temp_f: float,
    cargo_description: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Evaluate operational safety thresholds with cargo awareness.

    Black ice uses precip_mm_hr > 0, NOT >= PRECIP_THRESHOLD_MM_HR — any measurable precipitation
    at or below freezing is a black-ice risk, a materially lower bar than the heavy-precipitation
    threshold. Do not "simplify" this to share the same precip comparison as the storm check.

    Wind thresholds adapt based on trailer dynamics: empty or dry-van trailers have higher rollover
    risk in crosswinds (alerting at 35 mph), whereas heavy loaded flatbeds sustain up to 45 mph.
    """
    reasons: list[str] = []

    is_dry_van = bool(
        cargo_description and any(kw in cargo_description.lower() for kw in ("dry van", "empty"))
    )
    wind_threshold = DRY_VAN_WIND_THRESHOLD_MPH if is_dry_van else WIND_THRESHOLD_MPH

    if wind_speed_mph >= wind_threshold:
        trailer_note = " (dry-van rollover risk)" if is_dry_van else ""
        reasons.append(
            f"Wind {wind_speed_mph:.1f}mph exceeds {wind_threshold:.0f}mph threshold{trailer_note}"
        )

    if precip_mm_hr >= PRECIP_THRESHOLD_MM_HR:
        reasons.append(
            f"Precipitation {precip_mm_hr:.1f}mm/hr exceeds {PRECIP_THRESHOLD_MM_HR:.0f}mm/hr threshold"
        )

    if temp_f <= BLACK_ICE_TEMP_F and precip_mm_hr > 0:
        reasons.append(f"Black ice risk: {temp_f:.1f}°F with {precip_mm_hr:.1f}mm/hr precipitation")

    return bool(reasons), reasons
