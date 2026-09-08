from __future__ import annotations

from datetime import datetime, timezone

import polyline
import pydeck

from data import load_mock_shipments
from schemas import (
    CandidateRoute,
    Coordinate,
    DetourResult,
    HazardPolygon,
    RouteWeatherResult,
    WeatherObservation,
)
from ui.map_layers import build_deck


def test_build_deck_planned_route_only():
    shipment = load_mock_shipments()[0]
    deck = build_deck(shipment)

    assert isinstance(deck, pydeck.Deck)
    assert len(deck.layers) == 2  # PathLayer and ScatterplotLayer
    layer_types = [layer.type for layer in deck.layers]
    assert "PathLayer" in layer_types
    assert "ScatterplotLayer" in layer_types


def test_build_deck_with_weather_and_hazard_polygon():
    shipment = load_mock_shipments()[0]
    hazard = HazardPolygon(
        geojson={
            "type": "Polygon",
            "coordinates": [
                [[-96.9, 32.7], [-96.7, 32.7], [-96.7, 32.9], [-96.9, 32.9], [-96.9, 32.7]]
            ],
        },
        waypoint_name="Davenport, IA",
        reason_summary="Severe wind",
    )
    weather_res = RouteWeatherResult(
        shipment_id=shipment.shipment_id,
        observations=[
            WeatherObservation(
                waypoint_name="Davenport, IA",
                location=Coordinate(lat=41.5236, lon=-90.5776),
                valid_time=datetime.now(timezone.utc),
                wind_speed_mph=50.0,
                precip_mm_hr=0.0,
                temp_f=70.0,
                breached=True,
                reasons=["Wind 50mph"],
                hazard_polygon=hazard,
            )
        ],
    )
    deck = build_deck(shipment, weather_result=weather_res)
    layer_types = [layer.type for layer in deck.layers]
    assert "GeoJsonLayer" in layer_types


def test_build_deck_with_detour_route():
    shipment = load_mock_shipments()[0]
    valid_polyline = polyline.encode(
        [(41.8781, -87.6298), (41.5236, -90.5776), (39.7392, -104.9903)]
    )

    baseline = CandidateRoute(
        polyline=valid_polyline, distance_meters=1000, duration_seconds=600, intersects_hazard=True
    )
    chosen = CandidateRoute(
        polyline=valid_polyline, distance_meters=1100, duration_seconds=660, intersects_hazard=False
    )
    detour_res = DetourResult(
        shipment_id=shipment.shipment_id,
        status="clear_route_found",
        chosen_route=chosen,
        all_candidates=[baseline, chosen],
        still_hazardous=False,
        baseline_route=baseline,
        distance_delta_meters=100,
        eta_delta_seconds=60,
    )
    deck = build_deck(shipment, detour_result=detour_res)
    layer_types = [layer.type for layer in deck.layers]
    assert layer_types.count("PathLayer") == 2  # Planned route + Detour route


def test_build_deck_handles_malformed_polyline_gracefully():
    shipment = load_mock_shipments()[0]
    baseline = CandidateRoute(
        polyline="not_a_polyline",
        distance_meters=1000,
        duration_seconds=600,
        intersects_hazard=True,
    )
    chosen = CandidateRoute(
        polyline="also_not_valid",
        distance_meters=1100,
        duration_seconds=660,
        intersects_hazard=False,
    )
    detour_res = DetourResult(
        shipment_id=shipment.shipment_id,
        status="clear_route_found",
        chosen_route=chosen,
        all_candidates=[baseline, chosen],
        still_hazardous=False,
        baseline_route=baseline,
        distance_delta_meters=100,
        eta_delta_seconds=60,
    )
    # Must not raise an unhandled exception
    deck = build_deck(shipment, detour_result=detour_res)
    assert isinstance(deck, pydeck.Deck)
