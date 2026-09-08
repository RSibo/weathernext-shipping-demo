"""Tool declarations for the hand-rolled function-calling loop.

Both tools take only `shipment_id` — never raw coordinates or hazard geometry. Nested Pydantic
models DO introspect correctly via from_callable_with_api_option (verified directly against the
pinned google-genai version), so a richer signature was technically possible; shipment_id-only is
a deliberate choice, not a workaround. Gemini's role is to decide *when* to call a tool — every
number and every polygon involved comes from agent/loop.py's own state (the Shipment object and
the prior check_route_weather result), never from arguments Gemini fills in. This closes the
coordinate/geometry hallucination surface entirely, not just the metrics one the blueprint's
pseudocode called out.

These functions are schema sources only — agent/loop.py dispatches the real implementations
(tools.weather.run_weather_check, tools.routing.compute_detour) itself and never calls these.
"""

from __future__ import annotations

from google.genai import types


def check_route_weather(shipment_id: str) -> dict:
    """Check WeatherNext 3 forecasts along this shipment's full route (origin, waypoints,
    destination) at each point's estimated arrival time. Returns, per point, wind speed,
    precipitation, temperature, and whether an operational safety threshold was breached.

    Args:
        shipment_id: the shipment to check.
    """
    raise NotImplementedError("schema-only; see agent/loop.py")


def compute_detour_route(shipment_id: str) -> dict:
    """Compute an alternative route avoiding the hazardous weather cells check_route_weather
    already identified for this shipment. Only call this after check_route_weather reported
    any_breach = true for this shipment.

    Args:
        shipment_id: the shipment to compute a detour for.
    """
    raise NotImplementedError("schema-only; see agent/loop.py")


TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration.from_callable_with_api_option(
            callable=check_route_weather, api_option="VERTEX_AI"
        ),
        types.FunctionDeclaration.from_callable_with_api_option(
            callable=compute_detour_route, api_option="VERTEX_AI"
        ),
    ]
)
