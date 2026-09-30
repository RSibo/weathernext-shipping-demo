from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tests._synthetic import T0, line_plan, make_cube, shipment
from tools.assess import assess_route
from tools.facts import (
    FactSheet,
    fmt_cost,
    fmt_delta_distance,
    fmt_delta_duration,
    fmt_distance,
    fmt_duration,
    fmt_time,
    fmt_wind,
    fmt_window,
    weather_payload,
)

MDT = ZoneInfo("America/Denver")


def test_distance_and_duration_formats():
    assert fmt_distance(838_000) == "521 mi (838 km)"
    assert fmt_delta_distance(6_600) == "+4 mi (+7 km)"
    assert fmt_delta_distance(-51_200) == "−32 mi (−51 km)"
    assert fmt_duration(28_500) == "7 h 55 min"
    assert fmt_duration(3600) == "1 h"
    assert fmt_delta_duration(840) == "+14 min"


def test_times_are_lane_local_and_show_weekday_only_when_the_day_changes():
    ref = datetime(2026, 3, 12, 16, tzinfo=timezone.utc)  # Thu 10:00 MDT
    assert fmt_time(datetime(2026, 3, 12, 23, 55, tzinfo=timezone.utc), MDT, ref) == "17:55 MDT"
    assert fmt_time(datetime(2026, 3, 13, 7, 55, tzinfo=timezone.utc), MDT, ref) == "Fri 01:55 MDT"


def test_windows_collapse_to_one_time_when_a_breach_is_a_single_sample():
    ref = datetime(2026, 3, 12, 16, tzinfo=timezone.utc)
    a, b = datetime(2026, 3, 12, 18, 9, tzinfo=timezone.utc), datetime(2026, 3, 12, 18, 37, tzinfo=timezone.utc)
    assert fmt_window(a, b, MDT, ref) == "12:09–12:37 MDT"
    assert fmt_window(a, a.replace(second=40), MDT, ref) == "12:09 MDT"


def test_wind_rounding_never_reaches_the_threshold_from_below():
    assert fmt_wind(34.8, 35) == "34.8 mph"
    assert fmt_wind(34.4, 35) == "34 mph"
    assert fmt_wind(42.2, 35) == "42 mph"


def test_cost_is_mileage_only_and_never_negative():
    assert fmt_cost(6_600) == "$8 est."
    assert fmt_cost(-51_000) == "no added mileage cost"


def test_weather_payload_registers_every_display_string():
    sh = shipment()
    fs = FactSheet(sh)
    a = assess_route(line_plan("baseline", 41.5), T0, make_cube(), sh.cargo_description)
    p = weather_payload(fs, line_plan("baseline", 41.5), a, "Scenario · synthetic forecast")
    assert p["verdict"] == "hazardous" and p["wind_threshold"] == "35 mph"
    assert p["breaches"][0]["peak_wind"] == "45 mph"
    strings = fs.strings()
    for v in (p["distance"], p["drive_time"], p["arrive"], p["breaches"][0]["window"], p["depart"]):
        assert v in strings
