from __future__ import annotations

import base64
from datetime import timedelta

import numpy as np
import pytest

from schemas import RouteLeg
from tests._synthetic import T0, make_cube
from tools.cube import ForecastCube
from tools.geo import etas_along, haversine_km, nearest_place, resample


def test_resample_spacing_never_exceeds_step_and_keeps_endpoints():
    path = [(41.0, -111.0), (41.0, -110.0), (41.5, -109.0)]
    pts, km = resample(path, 5.0)
    assert np.all(np.diff(km) <= 5.0 + 1e-9)
    assert pts[0] == pytest.approx(path[0]) and pts[-1] == pytest.approx(path[-1])
    assert km[-1] == pytest.approx(haversine_km(41, -111, 41, -110) + haversine_km(41, -110, 41.5, -109), rel=1e-6)


def test_etas_follow_leg_durations_not_a_single_average_speed():
    km = np.array([0.0, 50.0, 100.0, 150.0, 200.0])
    # Leg 1 (first 100 km) is slow (2 h), leg 2 is fast (1 h).
    legs = [RouteLeg(distance_meters=100_000, duration_seconds=7200), RouteLeg(distance_meters=100_000, duration_seconds=3600)]
    etas = etas_along(km, 200.0, T0, 10_800, legs)
    assert [(e - T0).total_seconds() for e in etas] == pytest.approx([0, 3600, 7200, 9000, 10800])


def test_nearest_place_names_known_towns_and_falls_back_to_coordinates():
    assert nearest_place(41.59, -106.2) == "Arlington, WY"
    assert "°N" in nearest_place(10.0, -150.0)


def test_window_returns_none_outside_bbox_and_past_horizon():
    cube = make_cube(hours=12)
    assert cube.window(41.5, -107.0, T0 + timedelta(hours=3)) is not None
    assert cube.window(45.0, -107.0, T0 + timedelta(hours=3)) is None  # outside bbox
    assert cube.window(41.5, -107.0, T0 + timedelta(hours=20)) is None  # past horizon


def test_window_covers_eta_plus_minus_one_hour():
    cube = make_cube(storm_hours=(4, 4))  # storm only at valid hour +4
    hours = cube.window(41.5, -107.0, T0 + timedelta(hours=3))
    assert len(hours) == 3 and max(h.wind_mph for h in hours) == 45.0
    assert max(h.wind_mph for h in cube.window(41.5, -107.0, T0 + timedelta(hours=6))) == 10.0


def test_nan_cell_is_unmonitored_not_clean():
    cube = make_cube()
    yi, xi = cube.cell_index(41.5, -107.0)
    cube.wind_mph[:, yi, xi] = np.nan
    assert cube.window(41.5, -107.0, T0 + timedelta(hours=3)) is None


def test_wire_quantization_round_trip_and_no_data_is_zero():
    cube = make_cube(hours=4)
    cube.wind_mph[0, 0, 0] = np.nan
    wire = cube.wire("wind_mph")
    q = np.frombuffer(base64.b64decode(wire["data_b64"]), dtype=np.uint8).reshape(4, wire["ny"], wire["nx"])
    assert q[0, 0, 0] == 0
    decoded = wire["offset"] + (q[1:].astype(float) - 1) * wire["step"]
    assert np.abs(decoded - cube.wind_mph[1:]).max() <= wire["step"] / 2 + 1e-6


def test_npz_round_trip(tmp_path):
    cube = make_cube(hours=3)
    cube.save_npz(tmp_path / "c.npz")
    back = ForecastCube.load_npz(tmp_path / "c.npz")
    assert back.meta.init_time == cube.meta.init_time and back.meta.source == "scenario"
    np.testing.assert_array_equal(back.wind_mph, cube.wind_mph)
