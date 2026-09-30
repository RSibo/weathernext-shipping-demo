from __future__ import annotations

import polyline as polyline_lib
import pytest
import respx
from httpx import Response
from pydantic import ValidationError

from schemas import CandidateRoute, Coordinate, DetourRequest, DetourResult, HazardPolygon
from tools.polyline_codec import decode, to_linestring
from tools.routing import RoutingError, _filter_against_hazards, compute_detour


def test_to_linestring_swaps_lat_lon_to_lon_lat():
    # Asymmetric points so a swap bug is unmistakable: lat=10, lon=50 vs. lat=50, lon=10.
    encoded = polyline_lib.encode([(10.0, 50.0), (50.0, 10.0)])

    decoded = decode(encoded)
    assert decoded[0] == pytest.approx((10.0, 50.0), abs=1e-4)

    line = to_linestring(encoded)
    # shapely coords are (x, y) == (lon, lat) -> first point's x must be 50 (the longitude), not 10.
    assert line.coords[0] == pytest.approx((50.0, 10.0), abs=1e-4)
    assert line.coords[1] == pytest.approx((10.0, 50.0), abs=1e-4)


def _hazard_box(min_lon, min_lat, max_lon, max_lat) -> HazardPolygon:
    return HazardPolygon(
        geojson={
            "type": "Polygon",
            "coordinates": [
                [
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat],
                ]
            ],
        },
        waypoint_name="hazard-stop",
        reason_summary="wind",
    )


def _candidate_from_points(points_lat_lon, distance=1000, duration=600) -> CandidateRoute:
    encoded = polyline_lib.encode(points_lat_lon)
    return CandidateRoute(
        polyline=encoded,
        distance_meters=distance,
        duration_seconds=duration,
        intersects_hazard=False,
    )


def test_filter_marks_clean_candidate_as_not_intersecting():
    # Path runs far from the hazard box.
    clean = _candidate_from_points([(0.0, 0.0), (1.0, 1.0)])
    hazard = _hazard_box(50.0, 50.0, 51.0, 51.0)

    result = _filter_against_hazards([clean], [hazard])

    assert result[0].intersects_hazard is False
    assert result[0].intersection_length_deg == 0.0


def test_filter_marks_crossing_candidate_as_intersecting():
    # Path runs straight through the hazard box.
    crossing = _candidate_from_points([(0.0, 0.0), (10.0, 10.0)])
    hazard = _hazard_box(4.0, 4.0, 6.0, 6.0)

    result = _filter_against_hazards([crossing], [hazard])

    assert result[0].intersects_hazard is True
    assert result[0].intersection_length_deg > 0.0


def test_all_candidates_hazardous_picks_least_bad():
    heavy = _candidate_from_points([(0.0, 0.0), (10.0, 10.0)])  # long diagonal through hazard
    light = _candidate_from_points([(4.5, 4.5), (5.5, 5.5)])  # short clip through hazard
    hazard = _hazard_box(4.0, 4.0, 6.0, 6.0)

    filtered = _filter_against_hazards([heavy, light], [hazard])
    assert all(c.intersects_hazard for c in filtered)
    least_bad = min(filtered, key=lambda c: c.intersection_length_deg)
    assert least_bad.polyline == light.polyline


def _routes_response(candidates_lat_lon):
    return {
        "routes": [
            {
                "distanceMeters": 10000 + i,
                "duration": f"{600 + i * 10}s",
                "polyline": {"encodedPolyline": polyline_lib.encode(points)},
            }
            for i, points in enumerate(candidates_lat_lon)
        ]
    }


@respx.mock
def test_compute_detour_returns_clear_route_when_one_candidate_avoids_hazard():
    hazard = _hazard_box(4.0, 4.0, 6.0, 6.0)
    respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(
            200,
            json=_routes_response(
                [
                    [(0.0, 0.0), (10.0, 10.0)],  # crosses hazard
                    [(0.0, 0.0), (0.0, 20.0)],  # avoids hazard
                ]
            ),
        )
    )
    request = DetourRequest(
        shipment_id="ship-1",
        origin=Coordinate(lat=0.0, lon=0.0),
        destination=Coordinate(lat=10.0, lon=10.0),
        hazard_polygons=[hazard],
    )

    result = compute_detour(request, api_key="fake-key")

    assert result.status == "clear_route_found"
    assert result.still_hazardous is False
    assert result.chosen_route.intersects_hazard is False
    assert result.advisory_guidance is None
    assert len(result.all_candidates) == 2

    # baseline_route is the API's first (default, hazard-crossing) candidate; deltas are computed
    # deterministically against it, not left for Gemini to subtract.
    assert result.baseline_route.distance_meters == 10000
    assert result.distance_delta_meters == result.chosen_route.distance_meters - 10000
    assert result.eta_delta_seconds == result.chosen_route.duration_seconds - 600


@respx.mock
def test_compute_detour_flags_still_hazardous_when_no_clean_candidate():
    hazard = _hazard_box(-1.0, -1.0, 11.0, 11.0)  # covers the whole area
    respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(200, json=_routes_response([[(0.0, 0.0), (10.0, 10.0)]]))
    )
    request = DetourRequest(
        shipment_id="ship-1",
        origin=Coordinate(lat=0.0, lon=0.0),
        destination=Coordinate(lat=10.0, lon=10.0),
        hazard_polygons=[hazard],
    )

    result = compute_detour(request, api_key="fake-key")

    assert result.status == "no_clear_route_available"
    assert result.still_hazardous is True
    assert result.advisory_guidance is not None
    assert (
        "hold the vehicle" in result.advisory_guidance
        and "delay departure" in result.advisory_guidance
    )


@respx.mock
def test_compute_detour_raises_routing_error_on_http_failure():
    respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(500)
    )
    request = DetourRequest(
        shipment_id="ship-1",
        origin=Coordinate(lat=0.0, lon=0.0),
        destination=Coordinate(lat=10.0, lon=10.0),
        hazard_polygons=[],
    )

    with pytest.raises(RoutingError):
        compute_detour(request, api_key="fake-key")


def test_detour_result_rejects_hazardous_without_advisory_guidance():
    baseline = _candidate_from_points([(0.0, 0.0), (1.0, 1.0)])
    with pytest.raises(ValidationError, match="still_hazardous=True requires advisory_guidance"):
        DetourResult(
            shipment_id="ship-1",
            status="no_clear_route_available",
            chosen_route=baseline,
            all_candidates=[baseline],
            still_hazardous=True,
            baseline_route=baseline,
            distance_delta_meters=0,
            eta_delta_seconds=0,
            advisory_guidance=None,
        )


def test_detour_result_rejects_advisory_guidance_when_not_hazardous():
    baseline = _candidate_from_points([(0.0, 0.0), (1.0, 1.0)])
    with pytest.raises(ValidationError, match="advisory_guidance must be None"):
        DetourResult(
            shipment_id="ship-1",
            status="clear_route_found",
            chosen_route=baseline,
            all_candidates=[baseline],
            still_hazardous=False,
            baseline_route=baseline,
            distance_delta_meters=0,
            eta_delta_seconds=0,
            advisory_guidance="should not be set",
        )


def test_compute_detour_raises_without_api_key():
    # Empty string, not None: None would fall back to config.GOOGLE_MAPS_ROUTES_API_KEY, which may
    # be genuinely set in the environment running this test. "" exercises the falsy-key check
    # directly regardless of environment.
    request = DetourRequest(
        shipment_id="ship-1",
        origin=Coordinate(lat=0.0, lon=0.0),
        destination=Coordinate(lat=10.0, lon=10.0),
        hazard_polygons=[],
    )
    with pytest.raises(RoutingError):
        compute_detour(request, api_key="")


@respx.mock
def test_compute_detour_passes_intermediates_to_routes_api():
    import json

    route_mock = respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(
            200,
            json=_routes_response([[(0.0, 0.0), (5.0, 5.0), (10.0, 10.0)]]),
        )
    )
    request = DetourRequest(
        shipment_id="ship-1",
        origin=Coordinate(lat=0.0, lon=0.0),
        destination=Coordinate(lat=10.0, lon=10.0),
        intermediates=[Coordinate(lat=5.0, lon=5.0)],
        hazard_polygons=[],
    )

    result = compute_detour(request, api_key="fake-key")

    assert result.status == "clear_route_found"
    assert route_mock.called
    sent_body = json.loads(route_mock.calls.last.request.content)
    assert "intermediates" in sent_body
    assert sent_body["intermediates"] == [
        {"location": {"latLng": {"latitude": 5.0, "longitude": 5.0}}}
    ]
    # Routes API returns no alternatives when intermediates are present, so don't ask for them.
    assert sent_body["computeAlternativeRoutes"] is False


@respx.mock
def test_compute_detour_without_stops_requests_alternatives():
    import json

    route_mock = respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(200, json=_routes_response([[(0.0, 0.0), (10.0, 10.0)]]))
    )
    request = DetourRequest(
        shipment_id="ship-1", origin=Coordinate(lat=0.0, lon=0.0), destination=Coordinate(lat=10.0, lon=10.0),
        hazard_polygons=[],
    )
    compute_detour(request, api_key="fake-key")
    assert json.loads(route_mock.calls.last.request.content)["computeAlternativeRoutes"] is True


def _plan_response(descriptions):
    return {
        "routes": [
            {
                "description": d,
                "routeLabels": ["DEFAULT_ROUTE" if i == 0 else "DEFAULT_ROUTE_ALTERNATE"],
                "distanceMeters": 838_000 + i * 6_600,
                "duration": f"{28_500 + i * 840}s",
                "polyline": {"encodedPolyline": polyline_lib.encode([(40.7, -111.9), (41.0 - i, -108.0), (39.7, -105.0)])},
                "legs": [{"distanceMeters": 838_000 + i * 6_600, "duration": f"{28_500 + i * 840}s"}],
            }
            for i, d in enumerate(descriptions)
        ]
    }


@respx.mock
def test_compute_routes_ids_field_mask_and_traffic_unaware_body():
    import json

    from tools.routing import compute_routes

    mock = respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(200, json=_plan_response(["I-80 E", "I-70 E"]))
    )
    plans = compute_routes(Coordinate(lat=40.76, lon=-111.89), Coordinate(lat=39.74, lon=-104.99), alternatives=True, api_key="k")
    assert [p.route_id for p in plans] == ["baseline", "alt:i-70-e"]
    assert [p.source for p in plans] == ["default", "alternative"]
    assert plans[1].duration_seconds - plans[0].duration_seconds == 840
    req = mock.calls.last.request
    body = json.loads(req.content)
    assert body["routingPreference"] == "TRAFFIC_UNAWARE" and "departureTime" not in body
    assert body["computeAlternativeRoutes"] is True
    for field in ("routes.description", "routes.legs.duration", "routes.polyline.encodedPolyline"):
        assert field in req.headers["X-Goog-FieldMask"]


@respx.mock
def test_compute_routes_via_anchor_keeps_stops_and_is_pass_through():
    import json

    from tools.routing import compute_routes

    mock = respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(200, json=_plan_response(["I-70 E"]))
    )
    stop, via = Coordinate(lat=39.0, lon=-110.0), Coordinate(lat=39.1, lon=-108.45)
    plans = compute_routes(
        Coordinate(lat=40.76, lon=-111.89), Coordinate(lat=39.74, lon=-104.99), stops=[stop], via=via,
        via_label="I-70 via Grand Junction, CO", via_index=1, api_key="k",
    )
    body = json.loads(mock.calls.last.request.content)
    assert body["computeAlternativeRoutes"] is False
    assert [i.get("via", False) for i in body["intermediates"]] == [False, True]
    assert plans[0].route_id == "via:i-70-via-grand-junction-co" and plans[0].source == "via_anchor"


@respx.mock
def test_route_cache_serves_repeats_and_replay_mode_never_calls_the_api(tmp_path):
    from tools.routing import RouteCache, compute_routes

    mock = respx.post("https://routes.googleapis.com/directions/v2:computeRoutes").mock(
        return_value=Response(200, json=_plan_response(["I-80 E"]))
    )
    o, d = Coordinate(lat=40.76, lon=-111.89), Coordinate(lat=39.74, lon=-104.99)
    live = RouteCache(tmp_path, mode="live")
    compute_routes(o, d, cache=live, api_key="k")
    compute_routes(o, d, cache=live, api_key="k")
    assert mock.call_count == 1 and (live.live_calls, live.cached_calls) == (1, 1)

    replay = RouteCache(tmp_path, mode="replay")  # fresh process: reads the recorded file
    assert compute_routes(o, d, cache=replay)[0].description == "I-80 E"
    with pytest.raises(RoutingError):
        compute_routes(o, Coordinate(lat=35.0, lon=-100.0), cache=replay)
    assert mock.call_count == 1
