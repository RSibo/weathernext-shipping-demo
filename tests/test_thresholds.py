from __future__ import annotations

import pytest

from tools.thresholds import (
    BLACK_ICE_TEMP_F,
    PRECIP_THRESHOLD_MM_HR,
    WIND_THRESHOLD_MPH,
    evaluate_breach,
)


def test_no_breach_when_all_below_thresholds():
    breached, reasons = evaluate_breach(wind_speed_mph=10, precip_mm_hr=1, temp_f=60)
    assert breached is False
    assert reasons == []


def test_wind_breach_at_or_above_threshold():
    breached, reasons = evaluate_breach(
        wind_speed_mph=WIND_THRESHOLD_MPH, precip_mm_hr=0, temp_f=60
    )
    assert breached is True
    assert len(reasons) == 1
    assert "Wind" in reasons[0]


def test_wind_just_below_threshold_no_breach():
    breached, _ = evaluate_breach(
        wind_speed_mph=WIND_THRESHOLD_MPH - 0.1, precip_mm_hr=0, temp_f=60
    )
    assert breached is False


def test_precip_breach_at_or_above_threshold():
    breached, reasons = evaluate_breach(
        wind_speed_mph=0, precip_mm_hr=PRECIP_THRESHOLD_MM_HR, temp_f=60
    )
    assert breached is True
    assert len(reasons) == 1
    assert "Precipitation" in reasons[0]


def test_black_ice_breach_with_any_measurable_precip():
    # Deliberately far below PRECIP_THRESHOLD_MM_HR to prove black ice uses a lower bar.
    breached, reasons = evaluate_breach(wind_speed_mph=0, precip_mm_hr=0.1, temp_f=BLACK_ICE_TEMP_F)
    assert breached is True
    assert len(reasons) == 1
    assert "Black ice" in reasons[0]


def test_black_ice_requires_nonzero_precip():
    breached, _ = evaluate_breach(wind_speed_mph=0, precip_mm_hr=0, temp_f=BLACK_ICE_TEMP_F - 10)
    assert breached is False


def test_black_ice_not_triggered_above_freezing():
    breached, _ = evaluate_breach(wind_speed_mph=0, precip_mm_hr=5, temp_f=BLACK_ICE_TEMP_F + 1)
    assert breached is False


def test_multiple_simultaneous_breaches():
    breached, reasons = evaluate_breach(wind_speed_mph=50, precip_mm_hr=30, temp_f=20)
    assert breached is True
    assert len(reasons) == 3


@pytest.mark.parametrize(
    "wind,precip,temp,expected",
    [
        (0, 0, 100, False),
        (100, 0, 100, True),
        (0, 100, 100, True),
        (0, 0.01, 32, True),
    ],
)
def test_table_driven(wind, precip, temp, expected):
    breached, _ = evaluate_breach(wind, precip, temp)
    assert breached is expected


def test_cargo_aware_wind_threshold_for_dry_van():
    # 38 mph is below the default 45 mph threshold, but above the 35 mph dry van threshold
    breached_van, reasons_van = evaluate_breach(
        wind_speed_mph=38.0,
        precip_mm_hr=0.0,
        temp_f=70.0,
        cargo_description="Palletized electronics, dry van",
    )
    assert breached_van is True
    assert "dry-van rollover risk" in reasons_van[0]

    breached_flatbed, reasons_flatbed = evaluate_breach(
        wind_speed_mph=38.0, precip_mm_hr=0.0, temp_f=70.0, cargo_description="Bulk cement, flatbed"
    )
    assert breached_flatbed is False
    assert len(reasons_flatbed) == 0
