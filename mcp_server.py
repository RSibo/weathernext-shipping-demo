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

from schemas import DetourRequest, DetourResult, RouteWeatherRequest, RouteWeatherResult, Waypoint
from tools.routing import compute_detour
from tools.weather import run_weather_check

SERVER_NAME = "mcp-route-disruption-server"


def build_server() -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        version="0.1.0",
        instructions=(
            "Weather-aware freight routing tools backed by WeatherNext 3 (BigQuery GIS) and the "
            "Google Maps Routes API. check_route_weather reports deterministic threshold breaches "
            "and hazard polygons per waypoint; compute_detour_route finds an alternative route "
            "that avoids supplied hazard polygons, or the least-hazardous one if none is clean."
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
