"""Exercises the real tool bodies via ToolManager.call_tool — the same method MCPServer itself
uses to dispatch a real JSON-RPC tool call (mcp/server/mcpserver/tools/tool_manager.py) — instead
of reaching for a `.fn` closure or a made-up private attribute. Earlier versions of this file
introspected `server._tools`, which doesn't exist on the installed `mcp` SDK (the real attribute is
`server._tool_manager`); the lookup silently returned {}, `tool_func` stayed None, and both tests
passed while executing zero of the code they claimed to test — confirmed via `pytest --cov`
showing 0% coverage on the tool bodies despite a green run. `Context()` takes no required
arguments; our tools never touch it, so a bare instance is a legitimate stand-in for a real
request context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from mcp.server.mcpserver import Context

from mcp_server import build_server
from schemas import (
    CandidateRoute,
    Coordinate,
    DetourResult,
    RouteWeatherResult,
    WeatherObservation,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_build_server_registers_tools():
    server = build_server()
    assert server.name == "mcp-route-disruption-server"


@pytest.mark.asyncio
async def test_mcp_check_route_weather_tool_calls_real_implementation_and_forwards_cargo():
    mock_weather = RouteWeatherResult(
        shipment_id="mcp-test",
        observations=[
            WeatherObservation(
                waypoint_name="Stop1",
                location=Coordinate(lat=30.0, lon=-95.0),
                valid_time=_NOW,
                wind_speed_mph=10,
                precip_mm_hr=0,
                temp_f=70,
                breached=False,
            )
        ],
    )
    with patch("mcp_server.run_weather_check", return_value=mock_weather) as mocked:
        server = build_server()
        result = await server._tool_manager.call_tool(
            "check_route_weather",
            {
                "shipment_id": "mcp-test",
                "waypoints": [
                    {
                        "name": "Stop1",
                        "location": {"lat": 30.0, "lon": -95.0},
                        "eta": _NOW.isoformat(),
                    }
                ],
                "cargo_description": "dry van",
            },
            Context(),
        )

    assert mocked.called
    # This is the specific regression the private-attribute bug hid: cargo_description must
    # reach run_weather_check so the MCP path applies the same cargo-aware wind threshold the
    # in-process agent loop does (see agent/loop.py's _check_route_weather).
    forwarded_request = mocked.call_args[0][0]
    assert forwarded_request.cargo_description == "dry van"
    assert result.shipment_id == "mcp-test"
    assert result.any_breach is False


@pytest.mark.asyncio
async def test_mcp_compute_detour_route_tool_calls_real_implementation():
    baseline = CandidateRoute(
        polyline="poly", distance_meters=1000, duration_seconds=600, intersects_hazard=False
    )
    mock_detour = DetourResult(
        shipment_id="mcp-test",
        status="clear_route_found",
        chosen_route=baseline,
        all_candidates=[baseline],
        still_hazardous=False,
        baseline_route=baseline,
        distance_delta_meters=0,
        eta_delta_seconds=0,
    )
    with patch("mcp_server.compute_detour", return_value=mock_detour) as mocked:
        server = build_server()
        result = await server._tool_manager.call_tool(
            "compute_detour_route",
            {
                "request": {
                    "shipment_id": "mcp-test",
                    "origin": {"lat": 30.0, "lon": -95.0},
                    "destination": {"lat": 32.0, "lon": -96.0},
                    "hazard_polygons": [],
                }
            },
            Context(),
        )

    assert mocked.called
    assert result.status == "clear_route_found"


def test_mcp_server_cli_help():
    from mcp_server import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
