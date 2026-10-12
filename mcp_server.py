"""mcp-route-disruption-server: exposes check_route_weather and compute_detour_route over MCP.

This is a protocol adapter over tools.weather.run_weather_check (a live BigQuery check at the
caller's waypoints) and tools.routing.compute_detour. The in-process agent (agent/runner.py)
samples a forecast cube along the whole route instead (tools/assess.py), but both paths judge a
sample with the same tools.thresholds rules, so a waypoint that breaches for one breaches for the
other.

Unlike the agent's tools (which deliberately take only shipment_id, resolving coordinates from
internal Shipment state), this server has no notion of "the current shipment" — an external MCP
client supplies waypoints/coordinates directly, since it isn't operating inside this repo's
Shipment-scoped session.

Usage:

    python -m mcp_server                      # stdio (default)
    python -m mcp_server --transport sse --port 8932
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import anyio
from mcp.server import MCPServer

from pathlib import Path
from datetime import datetime, timezone
import shapely.geometry

import config
from schemas import (
    Coordinate,
    DetourRequest,
    DetourResult,
    HazardPolygon,
    RouteWeatherRequest,
    RouteWeatherResult,
    Waypoint,
    WeatherObservation,
)
from tools.cube import ForecastCube
from tools.routing import compute_detour
from tools.thresholds import evaluate_breach
from tools.weather import run_weather_check

SERVER_NAME = "mcp-route-disruption-server"


def _cube_weather_check(request: RouteWeatherRequest) -> RouteWeatherResult:
    """Evaluate waypoints against the local WeatherNext 3 ForecastCube (replay/cached scene)."""
    cube_path = Path(config.REPLAY_CUBE_PATH)
    if not cube_path.exists():
        raise RuntimeError(f"Replay cube not found at {cube_path}")
    cube = ForecastCube.load_npz(cube_path)
    observations: list[WeatherObservation] = []
    unmonitored: list[str] = []

    for wp in request.waypoints:
        eta = wp.eta.astimezone(timezone.utc)
        # If caller passes a current/future timestamp outside the replay window, map to the same
        # hour offset inside the 48h cube so ad-hoc assistant queries still resolve cleanly.
        query_time = eta
        if not cube.covers_time(query_time):
            span_s = int(cube.times_s[-1] - cube.times_s[0]) or 3600
            offset_s = int(eta.timestamp() - cube.times_s[0]) % span_s
            query_time = datetime.fromtimestamp(int(cube.times_s[0]) + offset_s, tz=timezone.utc)

        hours = cube.window(wp.location.lat, wp.location.lon, query_time, window_h=1.0)
        if not hours:
            unmonitored.append(wp.name)
            continue

        best = max(hours, key=lambda h: (h.wind_mph, h.precip_mm_hr))
        breached, reasons = evaluate_breach(
            wind_speed_mph=best.wind_mph,
            precip_mm_hr=best.precip_mm_hr,
            temp_f=best.temp_f,
            cargo_description=request.cargo_description,
        )
        hazard_polygon = None
        if breached:
            idx = cube.cell_index(wp.location.lat, wp.location.lon)
            if idx is not None:
                cell_box = shapely.geometry.box(*cube.cell_bounds(*idx)).buffer(
                    config.HAZARD_BUFFER_DEGREES
                )
                hazard_polygon = HazardPolygon(
                    geojson=shapely.geometry.mapping(cell_box),
                    waypoint_name=wp.name,
                    reason_summary="; ".join(reasons),
                )
        observations.append(
            WeatherObservation(
                waypoint_name=wp.name,
                location=wp.location,
                valid_time=best.t,
                wind_speed_mph=round(best.wind_mph, 1),
                precip_mm_hr=round(best.precip_mm_hr, 2),
                temp_f=round(best.temp_f, 1),
                breached=breached,
                reasons=reasons,
                hazard_polygon=hazard_polygon,
                forecast_issued_at=cube.meta.init_time,
            )
        )

    return RouteWeatherResult(
        shipment_id=request.shipment_id,
        observations=observations,
        unmonitored_waypoints=unmonitored if unmonitored else None,
    )


def build_server() -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        version="0.1.0",
        instructions=(
            "Weather-aware freight routing and forecast tools backed by WeatherNext 3 and the "
            "Google Maps Routes API. Use get_weather_forecast for general point/waypoint weather "
            "queries (e.g. from an executive assistant like Chloe), get_fleet_weather_status for "
            "active freight fleet disruption status, check_route_weather for shipment waypoint "
            "threshold breaches, and compute_detour_route to route around hazard polygons."
        ),
    )

    @server.tool(
        name="check_route_weather",
        title="Check route weather",
        description=(
            "Check WeatherNext 3 forecasts at each waypoint's estimated arrival time and report "
            "wind/precipitation/temperature plus deterministic safety-threshold breaches."
        ),
    )
    async def check_route_weather(
        shipment_id: str, waypoints: list[Waypoint], cargo_description: Optional[str] = None
    ) -> RouteWeatherResult:
        """Args: shipment_id — caller-assigned identifier, echoed back in the result.
        waypoints — ordered list of {name, location: {lat, lon}, eta} along the route.
        cargo_description — freeform cargo/trailer description (e.g. "dry van", "empty"); used to
        select the cargo-aware wind threshold (35mph vs 45mph). Must be supplied by the caller —
        this server has no Shipment record of its own to read it from, unlike the agent's
        in-process tool, which resolves it automatically from the shipment being reviewed."""
        request = RouteWeatherRequest(
            shipment_id=shipment_id, waypoints=waypoints, cargo_description=cargo_description
        )
        # BigQuery client calls are blocking, so they run in a worker thread, off the event loop.
        return await anyio.to_thread.run_sync(lambda: run_weather_check(request))

    @server.tool(
        name="get_weather_forecast",
        title="Get WeatherNext 3 forecast for locations",
        description=(
            "Query WeatherNext 3 forecast conditions (wind speed mph, precipitation mm/hr, "
            "temperature F, and safety threshold breaches) for one or more locations and ETAs. "
            "Uses the local WeatherNext 3 forecast cube first and falls back to live BigQuery."
        ),
    )
    async def get_weather_forecast(
        waypoints: list[Waypoint],
        cargo_description: Optional[str] = None,
        shipment_id: str = "adhoc-query",
    ) -> RouteWeatherResult:
        """Args: waypoints — list of {name, location: {lat, lon}, eta} to check.
        cargo_description — optional cargo/vehicle type ('dry van', 'empty', 'flatbed').
        shipment_id — optional caller correlation ID (default 'adhoc-query')."""
        request = RouteWeatherRequest(
            shipment_id=shipment_id, waypoints=waypoints, cargo_description=cargo_description
        )
        return await anyio.to_thread.run_sync(lambda: _cube_weather_check(request))

    @server.tool(
        name="get_fleet_weather_status",
        title="Get fleet weather disruption summary",
        description=(
            "Return the current WeatherNext 3 fleet scan summary and per-shipment weather risk "
            "assessments (CLEAR, MONITOR, ACTION) across all monitored freight lanes."
        ),
    )
    async def get_fleet_weather_status(shipment_id: Optional[str] = None) -> dict:
        """Args: shipment_id — optional shipment ID (e.g. 'SHP-2001') to filter to a single lane."""
        def _scan() -> dict:
            from api.fleet import scan_fleet
            from api.state import build_state

            st = build_state()
            scene = st.scene(None)
            if scene is None:
                return {"error": "No forecast scene available"}
            fl = scan_fleet(scene, st.routes)
            data = fl.model_dump(mode="json", exclude_none=True)
            if shipment_id:
                data["entries"] = [
                    e for e in data["entries"] if e["shipment"]["shipment_id"] == shipment_id
                ]
            for e in data["entries"]:
                e.pop("samples", None)
                if "route" in e and e["route"]:
                    e["route"].pop("path", None)
                    e["route"].pop("path_t_s", None)
            return data

        return await anyio.to_thread.run_sync(_scan)

    @server.tool(
        name="compute_detour_route",
        title="Compute detour route",
        description=(
            "Compute an alternative route between origin and destination that avoids the given "
            "hazard polygons, via Routes API alternativeRoutes filtered by shapely intersection."
        ),
    )
    async def compute_detour_route(request: DetourRequest) -> DetourResult:
        """Args: request — shipment_id, origin/destination coordinates, and the hazard polygons
        (typically taken from a prior check_route_weather result) to route around."""
        return await anyio.to_thread.run_sync(lambda: compute_detour(request))

    return server


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--transport", choices=("stdio", "sse", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8932)
    args = parser.parse_args(argv)

    server = build_server()

    # stderr, never stdout: on the stdio transport, stdout *is* the JSON-RPC channel.
    print(f"{SERVER_NAME} starting on {args.transport}", file=sys.stderr, flush=True)
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
