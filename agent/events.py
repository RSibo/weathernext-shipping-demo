"""The run event contract: everything the browser learns about an agent run arrives as one of these.

Server side, each event is a pydantic model; on the wire it is one SSE frame
`data: {"v":1,"run_id":...,"seq":N,"t_ms":...,"type":"...", ...payload}`. frontend/src/types.ts
mirrors these models field for field — change both together and bump EVENT_VERSION.

Numbers meant for people arrive as display strings from tools/facts.py (e.g. "+14 min"). Raw
numbers appear only where the UI must compute geometry or scales (paths, sample km, t_s).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Annotated, Any, Callable, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter

EVENT_VERSION = 1

Status = Literal["CLEAR", "MONITOR", "REROUTE", "HOLD"]
Verdict = Literal["clean", "at_risk", "hazardous", "unmonitored"]


# --- Shared views ------------------------------------------------------------------------------
class PlaceView(BaseModel):
    name: str
    lat: float
    lon: float


class ShipmentView(BaseModel):
    shipment_id: str
    lane: str
    carrier: Optional[str] = None
    cargo: Optional[str] = None
    wind_threshold: str
    wind_threshold_mph: float
    threshold_basis: str
    depart: str
    deadline: Optional[str] = None
    timezone: str
    depart_iso: Optional[str] = None  # UTC ISO 8601: places the truck on the forecast timeline
    deadline_iso: Optional[str] = None
    origin: PlaceView
    destination: PlaceView
    stops: list[PlaceView] = Field(default_factory=list)


class Provenance(BaseModel):
    source: Literal["live", "replay", "scenario"]
    init_time: str  # ISO 8601 UTC
    label: str  # "Replay · WeatherNext 3 forecast issued Mar 12 06:00Z"
    dataset: str = ""


class SegmentView(BaseModel):
    kind: Literal["breach", "near"]
    start_km: float
    end_km: float
    start_t_s: int
    end_t_s: int
    stretch: str
    place: str
    window: str
    length: str
    peak_wind: str
    hazards: list[str]
    peak_lat: float
    peak_lon: float


class RouteView(BaseModel):
    id: str
    description: str
    source: Literal["default", "alternative", "via_anchor"]
    via: Optional[str] = None
    distance: str
    duration: str
    arrive: str
    distance_km: float
    path: list[list[float]]  # [[lon, lat], ...] simplified
    path_t_s: list[int]  # seconds after departure at each path vertex


class SampleView(BaseModel):
    km: float
    lon: float
    lat: float
    t_s: int
    wind: Optional[float] = None
    precip: Optional[float] = None
    status: Literal["clear", "near", "breach", "unmonitored"]


class AssessmentView(BaseModel):
    route_id: str
    verdict: Verdict
    threshold_mph: float
    peak_wind: Optional[str] = None
    exposure: Optional[str] = None
    unmonitored_fraction: float
    segments: list[SegmentView] = Field(default_factory=list)
    near_segments: list[SegmentView] = Field(default_factory=list)


class CandidateView(BaseModel):
    option_id: str
    label: str
    route: RouteView
    assessment: AssessmentView
    delta_distance: str
    delta_duration: str
    cost: str
    meets_deadline: Optional[bool] = None


class DelayOptionView(BaseModel):
    option_id: str
    shift: str
    shift_hours: int
    depart: str
    arrive: str
    verdict: Literal["clean", "at_risk", "hazardous", "beyond_forecast"]
    peak_wind: Optional[str] = None
    meets_deadline: Optional[bool] = None


class Metric(BaseModel):
    key: str
    label: str
    value: str
    tone: Literal["neutral", "good", "warn", "bad"] = "neutral"


class TokenUsage(BaseModel):
    prompt: int = 0
    output: int = 0
    thoughts: int = 0


# --- Events ------------------------------------------------------------------------------------
class _Event(BaseModel):
    v: int = EVENT_VERSION
    run_id: str = ""
    seq: int = 0
    t_ms: int = 0


class RunStarted(_Event):
    type: Literal["run_started"] = "run_started"
    shipment: ShipmentView
    mode: Literal["live", "replay", "scenario"]
    provenance: Provenance
    model: str
    recorded: bool = False
    note: Optional[str] = None  # why a recorded run is playing, e.g. "Live runs are busy right now"


class RoutePlanned(_Event):
    type: Literal["route_planned"] = "route_planned"
    route: RouteView


class ModelTurn(_Event):
    type: Literal["model_turn"] = "model_turn"
    turn: int
    phase: Literal["start", "end"]
    model: str
    latency_ms: Optional[int] = None
    tokens: Optional[TokenUsage] = None
    thought: Optional[str] = None  # thought summary (Gemini include_thoughts)


class ToolCall(_Event):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    args: dict[str, Any]


class ToolResult(_Event):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    ok: bool
    latency_ms: int
    summary: str
    error: Optional[str] = None


class Guardrail(_Event):
    type: Literal["guardrail"] = "guardrail"
    kind: Literal["tool_blocked", "advisory_rejected", "advisory_accepted", "fallback_used"]
    message: str
    issues: list[str] = Field(default_factory=list)


class WeatherChecked(_Event):
    type: Literal["weather_checked"] = "weather_checked"
    assessment: AssessmentView
    method: str
    sample_spacing_km: float
    samples: list[SampleView]
    hazard_zones: Optional[dict] = None


class DetourComputed(_Event):
    type: Literal["detour_computed"] = "detour_computed"
    candidates: list[CandidateView]
    chosen_id: Optional[str] = None
    still_hazardous: bool
    anchors_tried: list[str] = Field(default_factory=list)


class DelayEvaluated(_Event):
    type: Literal["delay_evaluated"] = "delay_evaluated"
    options: list[DelayOptionView]
    earliest_safe_id: Optional[str] = None


class Advisory(_Event):
    type: Literal["advisory"] = "advisory"
    status: Status
    chosen_option_id: str
    headline: str
    recommendation: str
    rationale: str
    driver_message: str
    metrics: list[Metric] = Field(default_factory=list)
    source: Literal["gemini", "fallback"]
    grounded_numbers: int = 0


class RunCompleted(_Event):
    type: Literal["run_completed"] = "run_completed"
    status: Status
    turns: int
    total_ms: int
    llm_ms: int
    tools_ms: int
    tokens: TokenUsage
    routes_live: int = 0
    routes_cached: int = 0
    model: str


class RunError(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False
    fallback: Optional[Literal["golden"]] = None


RunEvent = Annotated[
    Union[
        RunStarted, RoutePlanned, ModelTurn, ToolCall, ToolResult, Guardrail, WeatherChecked,
        DetourComputed, DelayEvaluated, Advisory, RunCompleted, RunError,
    ],
    Field(discriminator="type"),
]
RUN_EVENT_ADAPTER: TypeAdapter = TypeAdapter(RunEvent)


def parse_event(data: dict | str) -> BaseModel:
    return RUN_EVENT_ADAPTER.validate_python(json.loads(data) if isinstance(data, str) else data)


def to_sse(event: BaseModel) -> str:
    return f"data: {event.model_dump_json(exclude_none=True)}\n\n"


class Emitter:
    """Stamps run_id / seq / t_ms onto events and hands them to a sink (list.append, a queue...)."""

    def __init__(self, sink: Callable[[BaseModel], None], run_id: Optional[str] = None):
        self.sink = sink
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._seq = 0
        self._t0 = time.monotonic()
        self._lock = threading.Lock()  # detour + delay tools run in parallel threads

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def __call__(self, event: _Event) -> _Event:
        with self._lock:
            self._seq += 1
            event.run_id, event.seq, event.t_ms = self.run_id, self._seq, self.elapsed_ms()
            self.sink(event)
        return event
