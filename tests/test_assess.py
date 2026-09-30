from __future__ import annotations

from datetime import timedelta

from tests._synthetic import T0, line_plan, make_cube
from tools.assess import assess_route

DRY_VAN = "Empty 53' dry van"
FLATBED = "Steel coils, flatbed"


def test_route_through_storm_at_eta_is_hazardous_with_named_segment():
    cube = make_cube()  # 45 mph over lon -108..-106 for hours 2-10
    a = assess_route(line_plan("baseline", 41.5), T0, cube, DRY_VAN)
    assert a.verdict == "hazardous"
    assert len(a.segments) == 1
    seg = a.segments[0]
    assert seg.peak_wind_mph == 45.0 and seg.hazards == ["wind"]
    # The storm spans ~2 deg of longitude along a 6 deg route -> ~1/3 of the route.
    assert 140 < seg.end_km - seg.start_km < 190
    assert a.hazard_zones["type"] in ("Polygon", "MultiPolygon")


def test_route_outside_storm_is_clean():
    a = assess_route(line_plan("south", 40.3), T0, make_cube(), DRY_VAN)
    assert a.verdict == "clean" and not a.segments and a.hazard_zones is None


def test_storm_that_has_passed_before_the_truck_arrives_is_not_a_hazard():
    # Storm ends at +10 h; leaving at +9 h the truck reaches lon -108 at +12 h (window 11-13 h).
    a = assess_route(line_plan("baseline", 41.5), T0 + timedelta(hours=9), make_cube(hours=24), DRY_VAN)
    assert a.verdict == "clean"


def test_same_storm_breaches_dry_van_but_only_warns_flatbed():
    cube = make_cube(storm_mph=40.0)
    assert assess_route(line_plan("baseline", 41.5), T0, cube, DRY_VAN).verdict == "hazardous"
    flat = assess_route(line_plan("baseline", 41.5), T0, cube, FLATBED)
    assert flat.verdict == "at_risk" and flat.near_segments and flat.wind_threshold_mph == 45


def test_route_past_the_forecast_horizon_is_unmonitored_never_clean():
    a = assess_route(line_plan("baseline", 41.5, hours=6), T0, make_cube(hours=4, storm_hours=(0, 0)), DRY_VAN)
    assert a.verdict == "unmonitored" and 0.35 < a.unmonitored_fraction < 0.5  # hours 3.5-6 of 6


def test_a_single_calm_cell_does_not_split_a_breach_into_two_segments():
    cube = make_cube()
    yi, xi = cube.cell_index(41.5, -107.0)
    cube.wind_mph[:, yi, xi] = 10.0
    a = assess_route(line_plan("baseline", 41.5), T0, cube, DRY_VAN)
    assert len(a.segments) == 1


def test_black_ice_ignores_trace_precip_along_a_route():
    cold = make_cube(storm_mph=10.0, precip=0.03, temp_f=25.0)
    assert assess_route(line_plan("baseline", 41.5), T0, cold, FLATBED).verdict == "clean"
    snowy = make_cube(storm_mph=10.0, precip=0.5, temp_f=25.0)
    a = assess_route(line_plan("baseline", 41.5), T0, snowy, FLATBED)
    assert a.verdict == "hazardous" and a.segments[0].hazards == ["black_ice"]
