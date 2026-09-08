"""PyDeck layers for the dispatcher map: planned route, chosen detour, hazard polygons, waypoints."""

from __future__ import annotations

from typing import Optional

import pydeck

from schemas import DetourResult, RouteWeatherResult, Shipment
from tools.polyline_codec import decode

_PLANNED_ROUTE_COLOR = [66, 133, 244]  # blue
_DETOUR_ROUTE_COLOR = [52, 168, 83]  # green
_HAZARD_FILL_COLOR = [234, 67, 53, 90]  # translucent red
_WAYPOINT_BREACHED_COLOR = [234, 67, 53]
_WAYPOINT_CLEAR_COLOR = [52, 168, 83]


def _bounding_view(shipment: Shipment) -> pydeck.ViewState:
    lats = [wp.location.lat for wp in shipment.all_waypoints()]
    lons = [wp.location.lon for wp in shipment.all_waypoints()]
    center_lat = (min(lats) + max(lats)) / 2
    center_lon = (min(lons) + max(lons)) / 2

    span = max(max(lats) - min(lats), max(lons) - min(lons), 0.5)
    zoom = 8 - span  # rough heuristic: wider bounding box -> lower zoom
    return pydeck.ViewState(latitude=center_lat, longitude=center_lon, zoom=max(zoom, 3), pitch=0)


def build_deck(
    shipment: Shipment,
    weather_result: Optional[RouteWeatherResult] = None,
    detour_result: Optional[DetourResult] = None,
) -> pydeck.Deck:
    layers = []

    planned_path = [[wp.location.lon, wp.location.lat] for wp in shipment.all_waypoints()]
    layers.append(
        pydeck.Layer(
            "PathLayer",
            data=[{"path": planned_path, "name": "Planned route"}],
            get_path="path",
            get_color=_PLANNED_ROUTE_COLOR,
            width_min_pixels=3,
        )
    )

    waypoint_data = [
        {
            "name": wp.name,
            "position": [wp.location.lon, wp.location.lat],
            "breached": False,
        }
        for wp in shipment.all_waypoints()
    ]

    if weather_result is not None:
        breached_names = {obs.waypoint_name for obs in weather_result.observations if obs.breached}
        for point in waypoint_data:
            point["breached"] = point["name"] in breached_names

        hazard_features = [
            {
                "type": "Feature",
                "geometry": obs.hazard_polygon.geojson,
                "properties": {"reason": obs.hazard_polygon.reason_summary},
            }
            for obs in weather_result.observations
            if obs.breached and obs.hazard_polygon is not None
        ]
        if hazard_features:
            layers.append(
                pydeck.Layer(
                    "GeoJsonLayer",
                    data={"type": "FeatureCollection", "features": hazard_features},
                    filled=True,
                    stroked=True,
                    get_fill_color=_HAZARD_FILL_COLOR,
                    get_line_color=[234, 67, 53],
                )
            )

    layers.append(
        pydeck.Layer(
            "ScatterplotLayer",
            data=waypoint_data,
            get_position="position",
            get_fill_color=f"breached ? {_WAYPOINT_BREACHED_COLOR} : {_WAYPOINT_CLEAR_COLOR}",
            get_radius=15000,
            pickable=True,
        )
    )

    if detour_result is not None:
        detour_path = [[lon, lat] for lat, lon in decode(detour_result.chosen_route.polyline)]
        layers.append(
            pydeck.Layer(
                "PathLayer",
                data=[{"path": detour_path, "name": "Detour route"}],
                get_path="path",
                get_color=_DETOUR_ROUTE_COLOR,
                width_min_pixels=4,
            )
        )

    return pydeck.Deck(
        layers=layers,
        initial_view_state=_bounding_view(shipment),
        map_style=None,
        tooltip={"text": "{name}"},
    )
