from __future__ import annotations

from datetime import timedelta

import pytest

import tools.detour as detour
from tests._synthetic import T0, line_plan, make_cube, shipment
from tools.assess import assess_route
from tools.delay import evaluate_departure_delay
from tools.routing import RoutingError


@pytest.fixture
def storm():
    return make_cube()


def _setup(monkeypatch, alternatives, via=None):
    calls = []

    def fake_compute_routes(origin, destination, *, stops=(), via=None, via_label=None, via_index=None,
                            alternatives=False, cache=None, api_key=None):
        calls.append({"via": via, "via_label": via_label, "alternatives": alternatives, "stops": list(stops)})
        if alternatives:
            return [line_plan("baseline", 41.5), *alt_plans]
        return via_fn(via_label)

    alt_plans = alternatives
    via_fn = via or (lambda label: [])
    monkeypatch.setattr(detour, "compute_routes", fake_compute_routes)
    monkeypatch.setattr(detour, "load_anchors", lambda: ())
    return calls


def test_fastest_clean_candidate_wins_and_differs_from_baseline(monkeypatch, storm):
    slow_clean = line_plan("alt:us-40-e", 40.2, hours=7.0, description="US-40 E", source="alternative")
    fast_clean = line_plan("alt:i-70-e", 40.4, hours=6.3, description="I-70 E", source="alternative")
    also_bad = line_plan("alt:wy-789", 41.6, hours=6.1, description="WY-789", source="alternative")
    _setup(monkeypatch, [slow_clean, fast_clean, also_bad])
    sh = shipment()
    base = line_plan("baseline", 41.5)
    d = detour.find_detour(sh, base, assess_route(base, T0, storm, sh.cargo_description), storm)
    assert d.chosen_id == "detour:i-70-e" and not d.still_hazardous
    assert d.chosen.delta_duration_seconds == int(0.3 * 3600)
    assert {c.option_id for c in d.candidates} == {"detour:i-70-e", "detour:us-40-e", "detour:wy-789"}


def test_no_clean_candidate_is_flagged_and_least_exposure_chosen(monkeypatch):
    cube = make_cube(storm_box=(-108.0, 40.0, -106.0, 42.0))  # storm covers every route
    partial = line_plan("alt:a", 41.5, lon0=-111.0, lon1=-107.5, hours=5.0, description="A", source="alternative")
    full = line_plan("alt:b", 41.4, hours=6.2, description="B", source="alternative")
    _setup(monkeypatch, [partial, full])
    sh = shipment()
    base = line_plan("baseline", 41.5)
    d = detour.find_detour(sh, base, assess_route(base, T0, cube, sh.cargo_description), cube)
    assert d.still_hazardous
    assert d.chosen_id == "detour:a"  # less exposure than baseline; never presented as safe


def test_duplicates_of_the_baseline_and_too_long_detours_are_dropped(monkeypatch, storm):
    dup = line_plan("alt:i-80-e", 41.5, description="I-80 E", source="alternative")
    too_long = line_plan("alt:long", 40.3, hours=9.0, description="Long way", source="alternative")
    _setup(monkeypatch, [dup, too_long])
    sh = shipment()
    base = line_plan("baseline", 41.5)
    d = detour.find_detour(sh, base, assess_route(base, T0, storm, sh.cargo_description), storm)
    assert d.candidates == [] and d.chosen_id is None and d.still_hazardous


def test_anchor_failures_are_tolerated_and_anchors_near_the_breach_excluded(monkeypatch, storm):
    anchors = (
        {"name": "Inside Storm", "highway": "I-80", "lat": 41.5, "lon": -107.0},
        {"name": "South A", "highway": "I-70", "lat": 40.4, "lon": -108.0},
        {"name": "South B", "highway": "US-40", "lat": 40.2, "lon": -108.0},
    )

    def via(label):
        if "South B" in label:
            raise RoutingError("boom")
        return [line_plan("via:i-70-via-south-a", 40.4, hours=6.4, description="I-70 E", source="via_anchor", via_label=label)]

    calls = _setup(monkeypatch, [], via)
    monkeypatch.setattr(detour, "load_anchors", lambda: anchors)
    sh = shipment(stops=[(41.5, -109.0)])  # stops -> no alternatives call
    base = line_plan("baseline", 41.5)
    d = detour.find_detour(sh, base, assess_route(base, T0, storm, sh.cargo_description), storm)
    assert not any(c["alternatives"] for c in calls)
    assert all("Inside Storm" not in (c["via_label"] or "") for c in calls)
    assert all(c["stops"] for c in calls)  # real stops are always kept
    assert d.chosen_id == "detour:via-i-70-via-south-a" and not d.still_hazardous
    assert len(d.anchors_tried) == 2


def test_delay_finds_earliest_safe_departure_and_checks_deadline():
    cube = make_cube(hours=24, storm_hours=(2, 8))
    sh = shipment(deadline_h=14.0)
    dl = evaluate_departure_delay(sh, line_plan("baseline", 41.5), cube, shifts_h=(2, 4, 8))
    by_id = {o.option_id: o for o in dl.options}
    assert by_id["delay:+2h"].verdict == "hazardous"
    assert by_id["delay:+8h"].verdict == "clean"
    assert dl.earliest_safe_id == "delay:+8h"
    assert by_id["delay:+8h"].meets_deadline is True  # arrives +14 h
    assert by_id["delay:+8h"].arrive_at == T0 + timedelta(hours=14)


def test_delay_past_the_forecast_horizon_is_beyond_forecast_never_clean():
    cube = make_cube(hours=10, storm_hours=(0, 0))
    dl = evaluate_departure_delay(shipment(), line_plan("baseline", 41.5), cube, shifts_h=(2, 8))
    by_id = {o.option_id: o for o in dl.options}
    assert by_id["delay:+2h"].verdict == "clean"
    assert by_id["delay:+8h"].verdict == "beyond_forecast"
