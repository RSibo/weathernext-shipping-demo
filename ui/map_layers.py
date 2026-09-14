"""PyDeck layers for the dispatcher map: planned route, chosen detour, hazard polygons, waypoints."""

from __future__ import annotations

import logging
import math
from typing import Optional

import pydeck

from schemas import DetourResult, RouteWeatherResult, Shipment
from tools.polyline_codec import decode

logger = logging.getLogger(__name__)

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

    # log2(360 / span) is the standard web-mercator "fit bounds" zoom (one world-width tile at
    # zoom 0 covers 360deg of longitude); the old linear heuristic (`8 - span`) clamped to its
    # zoom=3 floor for almost any real multi-state route (e.g. a ~1000km Miami-to-Atlanta lane),
    # which rendered at near-continental scale — wide enough that the hazard polygon and waypoint
    # markers below were visually imperceptible without the viewer manually zooming in first.
    span = max(max(lats) - min(lats), max(lons) - min(lons), 0.05)
    zoom = math.log2(360 / span) - 1  # -1 leaves margin so the route isn't flush against the edge
    return pydeck.ViewState(
        latitude=center_lat, longitude=center_lon, zoom=min(max(zoom, 3), 12), pitch=0
    )


def build_deck(
    shipment: Shipment,
    weather_result: Optional[RouteWeatherResult] = None,
    detour_result: Optional[DetourResult] = None,
) -> pydeck.Deck:
    layers = []

    planned_path = None
    if detour_result is not None and detour_result.baseline_route.polyline:
        try:
            planned_path = [
                [lon, lat] for lat, lon in decode(detour_result.baseline_route.polyline)
            ]
        except Exception:
            logger.warning(
                "Failed to decode baseline_route polyline %r; falling back to straight-line "
                "waypoint path.",
                detour_result.baseline_route.polyline,
                exc_info=True,
            )
            planned_path = None
    if planned_path is None:
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
                    get_line_width=200,
                    # Real-world hazard cells (tens of km) shrink to sub-pixel outlines at a
                    # zoomed-out multi-state route view; without a pixel floor the hazard the
                    # whole dashboard exists to highlight can silently disappear.
                    line_width_min_pixels=2,
                )
            )

    layers.append(
        pydeck.Layer(
            "ScatterplotLayer",
            data=waypoint_data,
            get_position="position",
            get_fill_color=f"breached ? {_WAYPOINT_BREACHED_COLOR} : {_WAYPOINT_CLEAR_COLOR}",
            get_radius=15000,
            # Same reasoning as the hazard layer's line_width_min_pixels: a 15km real-world radius
            # is a sub-pixel dot at a zoomed-out multi-state route view without this floor.
            radius_min_pixels=5,
            radius_max_pixels=20,
            pickable=True,
        )
    )

    if detour_result is not None:
        try:
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
        except Exception:
            logger.warning(
                "Failed to decode chosen_route polyline %r; detour route will not be rendered.",
                detour_result.chosen_route.polyline,
                exc_info=True,
            )

    return pydeck.Deck(
        layers=layers,
        initial_view_state=_bounding_view(shipment),
        map_style=None,
        tooltip={"text": "{name}"},
    )
