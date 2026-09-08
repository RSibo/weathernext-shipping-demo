"""Pydantic contracts shared by tools/, agent/, ui/, and mcp_server.py.

Design contract: every field on WeatherObservation and DetourResult that could tempt an LLM to
"reason about" raw numbers or geometry (breach flags, hazard polygons, still-hazardous status) is
computed in Python before the object is ever serialized to Gemini. Gemini reads these fields; it
must not be asked to recompute or second-guess them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Coordinate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class Waypoint(BaseModel):
    name: str
    location: Coordinate
    eta: datetime = Field(
        ..., description="Estimated arrival time at this waypoint, timezone-aware UTC."
    )

    @field_validator("eta")
    @classmethod
    def _reject_naive_datetime(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "eta must be timezone-aware (naive datetimes risk querying the wrong forecast hour)"
            )
        return v


class Shipment(BaseModel):
    shipment_id: str
    origin: Waypoint
    destination: Waypoint
    waypoints: list[Waypoint] = Field(default_factory=list, description="Intermediate stops only.")
    carrier: Optional[str] = None
    cargo_description: Optional[str] = None

    def all_waypoints(self) -> list[Waypoint]:
        """Origin, intermediate waypoints, and destination, in route order."""
        return [self.origin, *self.waypoints, self.destination]


class RouteWeatherRequest(BaseModel):
    shipment_id: str
    waypoints: list[Waypoint]
    cargo_description: Optional[str] = None


class HazardPolygon(BaseModel):
    geojson: dict = Field(..., description="A GeoJSON Polygon geometry dict (no Feature wrapper).")
    waypoint_name: str
    reason_summary: str


class WeatherObservation(BaseModel):
    waypoint_name: str
    location: Coordinate
    valid_time: datetime
    wind_speed_mph: float
    precip_mm_hr: float
    temp_f: float
    breached: bool
    reasons: list[str] = Field(default_factory=list)
    hazard_polygon: Optional[HazardPolygon] = None

    @model_validator(mode="after")
    def _breach_fields_consistent(self) -> "WeatherObservation":
        if self.breached and not self.reasons:
            raise ValueError("breached=True requires at least one reason")
        if self.breached and self.hazard_polygon is None:
            raise ValueError("breached=True requires a hazard_polygon")
        if not self.breached and self.hazard_polygon is not None:
            raise ValueError("hazard_polygon must be None when breached=False")
        return self


class RouteWeatherResult(BaseModel):
    shipment_id: str
    observations: list[WeatherObservation]
    any_breach: bool = False
    unmonitored_waypoints: Optional[list[str]] = Field(
        default=None,
        description="Waypoints for which WeatherNext forecast data could not be retrieved.",
    )

    @model_validator(mode="after")
    def _compute_any_breach(self) -> "RouteWeatherResult":
        self.any_breach = any(o.breached for o in self.observations)
        return self


class DetourRequest(BaseModel):
    shipment_id: str
    origin: Coordinate
    destination: Coordinate
    intermediates: list[Coordinate] = Field(
        default_factory=list,
        description="Intermediate waypoint locations along the route to preserve in candidate routes.",
    )
    hazard_polygons: list[HazardPolygon]


class CandidateRoute(BaseModel):
    polyline: str = Field(..., description="Google encoded polyline.")
    distance_meters: int
    duration_seconds: int
    intersects_hazard: bool
    intersection_length_deg: float = Field(
        0.0,
        description=(
            "Length of the candidate's overlap with the hazard union, in degrees (not meters) — "
            "used only for relative ranking among candidates from the same request, not as an "
            "absolute distance."
        ),
    )


class DetourResult(BaseModel):
    shipment_id: str
    status: Literal["clear_route_found", "no_clear_route_available"]
    chosen_route: CandidateRoute
    all_candidates: list[CandidateRoute]
    still_hazardous: bool
    baseline_route: CandidateRoute = Field(
        ...,
        description=(
            "The default route Routes API would have returned with no hazard-avoidance applied "
            "(the first candidate from computeRoutes) — used to compute the deltas below "
            "deterministically rather than asking the model to subtract two numbers."
        ),
    )
    distance_delta_meters: int
    eta_delta_seconds: int
    advisory_guidance: Optional[str] = Field(
        default=None,
        description="Deterministic operational guidance when no clean detour exists (e.g. staging hold recommendation).",
    )

    @model_validator(mode="after")
    def _deltas_match_chosen_minus_baseline(self) -> "DetourResult":
        expected_distance = self.chosen_route.distance_meters - self.baseline_route.distance_meters
        expected_eta = self.chosen_route.duration_seconds - self.baseline_route.duration_seconds
        if (
            self.distance_delta_meters != expected_distance
            or self.eta_delta_seconds != expected_eta
        ):
            raise ValueError(
                "distance_delta_meters/eta_delta_seconds must equal chosen_route minus baseline_route"
            )
        return self

    @model_validator(mode="after")
    def _status_matches_flag(self) -> "DetourResult":
        expected = self.status == "no_clear_route_available"
        if self.still_hazardous != expected:
            raise ValueError("still_hazardous must match status == 'no_clear_route_available'")
        return self

    @model_validator(mode="after")
    def _advisory_guidance_matches_hazardous_flag(self) -> "DetourResult":
        if self.still_hazardous and not self.advisory_guidance:
            raise ValueError("still_hazardous=True requires advisory_guidance to be set")
        if not self.still_hazardous and self.advisory_guidance is not None:
            raise ValueError("advisory_guidance must be None when still_hazardous=False")
        return self
