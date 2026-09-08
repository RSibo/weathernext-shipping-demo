"""Hand-rolled google-genai function-calling loop.

Automatic function calling is explicitly disabled (AutomaticFunctionCallingConfig(disable=True))
so this loop's own dispatcher — not the SDK's — decides whether a tool call actually runs. That's
what makes the Mandatory Rule #1 guardrail in _AdvisoryRun effective rather than decorative:
without disabling it, google-genai would execute tool calls itself before this code ever saw them.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from google import genai
from google.genai import types

import config
from agent.system_instruction import SYSTEM_INSTRUCTION
from agent.tool_schemas import TOOLS
from schemas import DetourRequest, DetourResult, RouteWeatherRequest, RouteWeatherResult, Shipment
from tools.routing import RoutingError, compute_detour
from tools.weather import run_weather_check

MAX_TURNS = 6


class _AdvisoryRun:
    """Per-shipment dispatch state for one run_advisory() call. Not reused across shipments —
    last_weather_result must not leak between unrelated runs."""

    def __init__(
        self, shipment: Shipment, weather_result_override: Optional[RouteWeatherResult] = None
    ):
        self.shipment = shipment
        self.weather_result_override = weather_result_override
        self.last_weather_result: Optional[RouteWeatherResult] = None
        self.last_detour_result: Optional[DetourResult] = None
        self.last_error: Optional[str] = None

    def dispatch(self, name: str, args: dict) -> dict:
        try:
            if name == "check_route_weather":
                return {"result": self._check_route_weather(args)}
            if name == "compute_detour_route":
                return {"result": self._compute_detour_route(args)}
            self.last_error = f"Unknown tool '{name}'"
            return {"error": self.last_error}
        except GuardrailViolation as exc:
            self.last_error = str(exc)
            return {"error": str(exc)}
        except RoutingError as exc:
            self.last_error = str(exc)
            return {"error": str(exc)}
        # Deliberately NOT `except Exception`: an unexpected failure (a real BigQuery error, a
        # schema mismatch, a bug) must crash loudly, not get silently rewritten into a plausible-
        # looking tool-error string Gemini has to improvise around. GuardrailViolation/RoutingError
        # are the only two exception types this dispatcher understands well enough to convert into
        # a function-response error; everything else propagates.

    def _check_shipment_id(self, args: dict) -> None:
        shipment_id = args.get("shipment_id")
        if shipment_id != self.shipment.shipment_id:
            raise GuardrailViolation(
                f"shipment_id '{shipment_id}' does not match the shipment under review "
                f"('{self.shipment.shipment_id}')"
            )

    def _check_route_weather(self, args: dict) -> dict:
        self._check_shipment_id(args)
        if self.weather_result_override is not None:
            self.last_weather_result = self.weather_result_override
        else:
            request = RouteWeatherRequest(
                shipment_id=self.shipment.shipment_id,
                waypoints=self.shipment.all_waypoints(),
                cargo_description=self.shipment.cargo_description,
            )
            self.last_weather_result = run_weather_check(request)
        dumped = self.last_weather_result.model_dump(mode="json")
        if dumped.get("unmonitored_waypoints") is None:
            dumped.pop("unmonitored_waypoints", None)
        return dumped

    def _compute_detour_route(self, args: dict) -> dict:
        self._check_shipment_id(args)

        # Mandatory Rule #1, enforced here in Python, not just in the prompt text: never compute
        # a detour before a check_route_weather call in this same run reported a breach.
        if self.last_weather_result is None:
            raise GuardrailViolation(
                "Mandatory rule violated: call check_route_weather before compute_detour_route."
            )
        if not self.last_weather_result.any_breach:
            raise GuardrailViolation(
                "Mandatory rule violated: check_route_weather reported any_breach=false; "
                "do not compute a detour for a clear route."
            )

        hazard_polygons = [
            obs.hazard_polygon
            for obs in self.last_weather_result.observations
            if obs.breached and obs.hazard_polygon is not None
        ]
        request = DetourRequest(
            shipment_id=self.shipment.shipment_id,
            origin=self.shipment.origin.location,
            destination=self.shipment.destination.location,
            intermediates=[wp.location for wp in self.shipment.waypoints],
            hazard_polygons=hazard_polygons,
        )
        result = compute_detour(request)
        self.last_detour_result = result
        dumped = result.model_dump(mode="json")
        if dumped.get("advisory_guidance") is None:
            dumped.pop("advisory_guidance", None)
        return dumped


class GuardrailViolation(RuntimeError):
    """A tool call that violated a MANDATORY RULE. Fed back to Gemini as a function error so it
    can self-correct on the next turn, rather than silently executed or silently dropped."""


def _shipment_summary(shipment: Shipment) -> str:
    lines = [f"Shipment {shipment.shipment_id}"]
    if shipment.carrier:
        lines.append(f"Carrier: {shipment.carrier}")
    if shipment.cargo_description:
        lines.append(f"Cargo: {shipment.cargo_description}")
    for wp in shipment.all_waypoints():
        lines.append(f"- {wp.name} ({wp.location.lat}, {wp.location.lon}) ETA {wp.eta.isoformat()}")
    return "\n".join(lines)


_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=config.GOOGLE_GENAI_USE_VERTEXAI,
            project=config.PROJECT_ID,
            location=config.VERTEX_LOCATION,
        )
    return _client


class AdvisoryResult(NamedTuple):
    text: str
    weather_result: Optional[RouteWeatherResult]
    detour_result: Optional[DetourResult]
    error: Optional[str] = None


def run_advisory_with_context(
    shipment: Shipment,
    *,
    client: Optional[genai.Client] = None,
    weather_result_override: Optional[RouteWeatherResult] = None,
) -> AdvisoryResult:
    """Runs the reasoning loop for one shipment, returning the advisory text alongside the
    underlying weather/detour results the loop itself computed — so a caller (e.g. the UI) can
    render the same data the advisory was based on, instead of re-querying BigQuery/Routes
    separately and risking the two views diverging."""
    genai_client = client if client is not None else _get_client()
    run = _AdvisoryRun(shipment, weather_result_override=weather_result_override)

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=_shipment_summary(shipment))])
    ]
    gen_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[TOOLS],
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for _ in range(MAX_TURNS):
        response = genai_client.models.generate_content(
            model=config.GEMINI_MODEL, contents=contents, config=gen_config
        )
        function_calls = response.function_calls
        if not function_calls:
            return AdvisoryResult(
                response.text, run.last_weather_result, run.last_detour_result, run.last_error
            )

        contents.append(response.candidates[0].content)

        response_parts = [
            types.Part.from_function_response(
                name=call.name, response=run.dispatch(call.name, dict(call.args or {}))
            )
            for call in function_calls
        ]
        contents.append(types.Content(role="user", parts=response_parts))

    raise RuntimeError(
        f"Advisory loop for shipment {shipment.shipment_id} did not terminate within {MAX_TURNS} turns"
    )


def run_advisory(
    shipment: Shipment,
    *,
    client: Optional[genai.Client] = None,
    weather_result_override: Optional[RouteWeatherResult] = None,
) -> str:
    """Runs the reasoning loop for one shipment and returns just the dispatcher advisory text."""
    return run_advisory_with_context(
        shipment, client=client, weather_result_override=weather_result_override
    ).text
