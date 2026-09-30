"""The dispatch agent: a Gemini function-calling loop that narrates itself as run events.

run_advisory_events() drives one shipment from "is this route safe?" to a validated advisory and
emits agent/events.py events as it goes, so the browser can show the reasoning while it happens.

- Gemini decides which tools to call (mode ANY: every turn is a tool call) and writes the advisory;
  Python runs the tools, enforces the rules and checks every number (agent/advisory.py).
- Automatic function calling is off, so this dispatcher, not the SDK, decides whether a call runs.
- Nothing Gemini does can leave the run without an answer: if it fails validation twice, runs out
  of turns or time, or Vertex AI is unavailable, the runner completes the analysis itself and
  emits the deterministic template advisory, labelled as such.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal, Optional

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

import config
from agent import events as ev
from agent.advisory import AdvisorySubmission, Findings, fallback_advisory, metrics_for, validate
from agent.prompt import SYSTEM_INSTRUCTION, TOOLS, opening_message
from agent.views import (
    assessment_view,
    candidate_views,
    delay_views,
    provenance,
    route_view,
    samples_view,
    shipment_view,
)
from schemas import Shipment
from tools import delay as delay_tool
from tools import detour as detour_tool
from tools.assess import assess_route
from tools.cube import ForecastCube
from tools.facts import FactSheet, delay_payload, detour_payload, weather_payload
from tools.routing import RouteCache, RoutingError, compute_routes

MAX_TURNS = 8
MAX_REJECTIONS = 1  # one retry after a rejected advisory, then the template
RUN_BUDGET_S = 45.0  # past this the template advisory is better theatre than a spinner
CALL_TIMEOUT_S = 25.0
ANALYSIS_TOOLS = ("check_route_weather", "compute_detour_route", "evaluate_departure_delay")


class RunCancelled(Exception):
    """The client went away; stop spending Gemini tokens."""


class ToolBlocked(Exception):
    """A tool call that breaks a rule. Returned to Gemini as an error so it can correct itself."""


class _UseFallback(Exception):
    pass


@dataclass
class RunContext:
    shipment: Shipment
    cube: ForecastCube
    routes: RouteCache  # a per-run RouteCache.scoped() view, so run_completed counts only this run's calls
    emit: ev.Emitter
    mode: Literal["live", "replay", "scenario"] = "replay"
    cancel: threading.Event = field(default_factory=threading.Event)
    now: Optional[datetime] = None
    api_key: Optional[str] = None


_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=config.GOOGLE_GENAI_USE_VERTEXAI,
            project=config.PROJECT_ID,
            location=config.VERTEX_LOCATION,
            http_options=types.HttpOptions(
                timeout=30_000,
                # Retry only fast failures; a 504 already cost a full timeout, so fall back instead.
                retry_options=types.HttpRetryOptions(
                    attempts=2, initial_delay=1.0, max_delay=4.0, http_status_codes=[429, 503]
                ),
            ),
        )
    return _client


def _gen_config(model: str, timeout_s: float = CALL_TIMEOUT_S) -> types.GenerateContentConfig:
    # Gemini 3 takes thinking_level; 2.5 (the fallback) only understands thinking_budget.
    thinking = (
        types.ThinkingConfig(thinking_level=config.GEMINI_THINKING_LEVEL, include_thoughts=True)
        if model.startswith("gemini-3")
        else types.ThinkingConfig(thinking_budget=1024, include_thoughts=True)
    )
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[TOOLS],
        temperature=1.0,  # Gemini 3 is tuned for 1.0; low temperatures can make it loop
        thinking_config=thinking,
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )


class _Run:
    def __init__(self, ctx: RunContext, client: genai.Client):
        self.ctx = ctx
        self.client = client
        self.emit: Callable = ctx.emit
        self.fs = FactSheet(ctx.shipment)
        self.f = Findings(ctx.shipment)
        self.model = config.GEMINI_MODEL
        self.tokens = ev.TokenUsage()
        self.llm_ms = 0
        self.tools_ms = 0
        self.turns = 0
        self.rejections = 0
        self._sys_calls = 0
        self._tools_lock = threading.Lock()

    # --- Tools ------------------------------------------------------------------------------------
    def check_route_weather(self) -> tuple[dict, str]:
        s, c = self.ctx.shipment, self.ctx.cube
        plans = compute_routes(
            s.origin.location, s.destination.location, stops=[w.location for w in s.waypoints],
            cache=self.ctx.routes, api_key=self.ctx.api_key,
        )
        if not plans:
            raise RoutingError("Routes API returned no route for this lane")
        plan = plans[0]
        a = assess_route(plan, s.origin.eta, c, s.cargo_description)
        self.f.baseline, self.f.assessment = plan, a
        payload = weather_payload(self.fs, plan, a, self.fs.get("forecast"))
        self.emit(ev.RoutePlanned(route=route_view(self.fs, plan, s.origin.eta)))
        self.emit(ev.WeatherChecked(
            assessment=assessment_view(self.fs, a), method=self.fs.get("method"),
            sample_spacing_km=a.sample_spacing_km, samples=samples_view(a), hazard_zones=a.hazard_zones,
        ))
        n = len(a.segments)
        summary = f"{a.verdict} · peak {payload.get('peak_wind') or 'n/a'} vs {payload['wind_threshold']} limit"
        if n:
            worst = max(a.segments, key=lambda seg: seg.peak_wind_mph)
            summary += f" · {n} breach stretch{'es' if n > 1 else ''}, worst near {worst.near_place}"
        return payload, summary + f" · {len(a.samples)} points"

    def _require_hazard(self, name: str) -> None:
        if self.f.assessment is None:
            raise ToolBlocked(f"call check_route_weather before {name}")
        if self.f.assessment.verdict != "hazardous":
            raise ToolBlocked(
                f"check_route_weather returned verdict '{self.f.assessment.verdict}'; {name} is only for hazardous routes"
            )

    def compute_detour_route(self) -> tuple[dict, str]:
        self._require_hazard("compute_detour_route")
        s = self.ctx.shipment
        d = detour_tool.find_detour(
            s, self.f.baseline, self.f.assessment, self.ctx.cube, cache=self.ctx.routes, api_key=self.ctx.api_key
        )
        self.f.detour = d
        payload = detour_payload(self.fs, d)
        self.emit(ev.DetourComputed(
            candidates=candidate_views(self.fs, d), chosen_id=d.chosen_id, still_hazardous=d.still_hazardous,
            anchors_tried=d.anchors_tried,
        ))
        n = len(d.candidates)
        if d.chosen and not d.still_hazardous:
            k = f"option.{d.chosen_id}"
            best = f"fastest safe: {d.chosen.plan.description} ({self.fs.get(k + '.delta_duration')})"
        else:
            best = "no candidate avoids the hazard"
        return payload, f"{n} candidate{'s' if n != 1 else ''} checked · {best}"

    def evaluate_departure_delay(self) -> tuple[dict, str]:
        self._require_hazard("evaluate_departure_delay")
        dl = delay_tool.evaluate_departure_delay(self.ctx.shipment, self.f.baseline, self.ctx.cube)
        self.f.delay = dl
        payload = delay_payload(self.fs, dl)
        self.emit(ev.DelayEvaluated(options=delay_views(self.fs, dl), earliest_safe_id=dl.earliest_safe_id))
        if dl.earliest_safe_id:
            o = next(o for o in dl.options if o.option_id == dl.earliest_safe_id)
            late = " (misses deadline)" if o.meets_deadline is False else ""
            best = f"earliest safe: +{o.shift_hours} h, arrive {self.fs.time(o.arrive_at)}{late}"
        else:
            best = "no safe departure inside the forecast"
        return payload, f"{len(dl.options)} departures checked · {best}"

    def call_tool(self, call_id: str, name: str, args: dict, *, announce: bool = True) -> dict:
        self._check_cancel()
        if announce:
            self.emit(ev.ToolCall(call_id=call_id, name=name, args=args))
        t0 = time.monotonic()
        ok, summary, error, response = False, "", None, {}
        try:
            if args.get("shipment_id") != self.ctx.shipment.shipment_id:
                raise ToolBlocked(
                    f"shipment_id '{args.get('shipment_id')}' is not the shipment under review "
                    f"('{self.ctx.shipment.shipment_id}')"
                )
            fn = {n: getattr(self, n) for n in ANALYSIS_TOOLS}.get(name)
            if fn is None:
                raise ToolBlocked(f"unknown tool '{name}'")
            payload, summary = fn()
            with self._tools_lock:
                self.f.tools_called.append(name)
            ok, response = True, {"result": payload}
        except ToolBlocked as exc:
            error = str(exc)
            self.emit(ev.Guardrail(kind="tool_blocked", message=error))
            response = {"error": error}
        except RoutingError as exc:
            error = str(exc)
            response = {"error": error}
        ms = int((time.monotonic() - t0) * 1000)
        with self._tools_lock:
            self.tools_ms += ms
        self.emit(ev.ToolResult(call_id=call_id, name=name, ok=ok, latency_ms=ms, summary=summary or error or "", error=error))
        return response

    # --- Advisory -----------------------------------------------------------------------------------
    def submit(self, call_id: str, args: dict) -> tuple[bool, dict]:
        self.emit(ev.ToolCall(call_id=call_id, name="submit_advisory", args=args))
        t0 = time.monotonic()
        issues: list[str]
        sub = None
        if args.get("shipment_id") not in (None, self.ctx.shipment.shipment_id):
            issues = [f"shipment_id must be {self.ctx.shipment.shipment_id}"]
        else:
            try:
                sub = AdvisorySubmission.model_validate({k: v for k, v in args.items() if k != "shipment_id"})
            except ValidationError as exc:
                issues = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        grounded = 0
        if sub is not None:
            v = validate(self.f, self.fs, sub)
            issues, grounded = v.issues, v.grounded_numbers
        ms = int((time.monotonic() - t0) * 1000)
        if not issues:
            self.emit(ev.Guardrail(
                kind="advisory_accepted",
                message=f"Decision allowed by the evidence · {grounded}/{grounded} numbers traced to tool results",
            ))
            self.emit(ev.ToolResult(call_id=call_id, name="submit_advisory", ok=True, latency_ms=ms,
                                    summary=f"{sub.status} · accepted"))
            self._finish(sub, "gemini", grounded)
            return True, {"result": "accepted"}
        self.rejections += 1
        self.emit(ev.Guardrail(kind="advisory_rejected", message="Advisory rejected; returned to Gemini", issues=issues))
        self.emit(ev.ToolResult(call_id=call_id, name="submit_advisory", ok=False, latency_ms=ms,
                                summary=f"rejected · {len(issues)} issue{'s' if len(issues) != 1 else ''}", error="; ".join(issues)))
        if self.rejections > MAX_REJECTIONS:
            raise _UseFallback("Gemini's advisory failed validation twice")
        return False, {"error": "advisory rejected; fix every issue and call submit_advisory again", "issues": issues}

    def _finish(self, sub: AdvisorySubmission, source: Literal["gemini", "fallback"], grounded: int) -> None:
        self.emit(ev.Advisory(
            status=sub.status, chosen_option_id=sub.chosen_option_id, headline=sub.headline,
            recommendation=sub.recommendation, rationale=sub.rationale, driver_message=sub.driver_message,
            metrics=metrics_for(self.f, self.fs, sub.status, sub.chosen_option_id), source=source,
            grounded_numbers=grounded,
        ))
        self.status = sub.status
        self.emit(ev.RunCompleted(
            status=sub.status, turns=self.turns, total_ms=self.emit.elapsed_ms(), llm_ms=self.llm_ms,
            tools_ms=self.tools_ms, tokens=self.tokens, routes_live=self.ctx.routes.live_calls,
            routes_cached=self.ctx.routes.cached_calls, model=self.model,
        ))

    def fallback(self, reason: str) -> str:
        """Finish the analysis without Gemini and emit the template advisory."""
        self.emit(ev.Guardrail(kind="fallback_used", message=f"{reason}: using the template advisory"))

        def sys_call(name: str) -> None:
            self._sys_calls += 1
            self.call_tool(f"sys-{self._sys_calls}", name, {"shipment_id": self.ctx.shipment.shipment_id})

        if self.f.assessment is None:
            sys_call("check_route_weather")
        if self.f.assessment is None:
            self.emit(ev.RunError(code="analysis_failed", message="The route could not be planned or checked.",
                                  retryable=True, fallback="golden"))
            return "ERROR"
        if self.f.assessment.verdict == "hazardous":
            if self.f.detour is None:
                sys_call("compute_detour_route")
            if self.f.delay is None:
                sys_call("evaluate_departure_delay")
            if self.f.detour is None or self.f.delay is None:
                self.emit(ev.RunError(code="analysis_failed", message="Detour or delay analysis failed.",
                                      retryable=True, fallback="golden"))
                return "ERROR"
        sub = fallback_advisory(self.f, self.fs)
        v = validate(self.f, self.fs, sub)
        self._finish(sub, "fallback", v.grounded_numbers)
        return sub.status

    # --- Loop ---------------------------------------------------------------------------------------
    def _check_cancel(self) -> None:
        if self.ctx.cancel.is_set():
            raise RunCancelled()

    def _generate(self, contents: list[types.Content], timeout_s: float) -> types.GenerateContentResponse:
        try:
            return self.client.models.generate_content(
                model=self.model, contents=contents, config=_gen_config(self.model, timeout_s)
            )
        except genai_errors.ClientError as exc:
            if exc.code == 404 and self.model != config.GEMINI_FALLBACK_MODEL:
                self.model = config.GEMINI_FALLBACK_MODEL
                return self.client.models.generate_content(
                    model=self.model, contents=contents, config=_gen_config(self.model, timeout_s)
                )
            raise

    def _dispatch(self, calls: list[types.FunctionCall]) -> tuple[bool, list[types.Part]]:
        """Run one turn's calls. The weather check runs first (the others depend on it), the rest in
        parallel, submit_advisory last so it sees this turn's findings. Returns (finished, responses)."""
        ids = [c.id or f"call-{self.turns}-{i}" for i, c in enumerate(calls)]
        responses: dict[int, dict] = {}
        order = sorted(range(len(calls)), key=lambda i: {"check_route_weather": 0, "submit_advisory": 2}.get(calls[i].name, 1))
        first = [i for i in order if calls[i].name == "check_route_weather"]
        middle = [i for i in order if calls[i].name not in ("check_route_weather", "submit_advisory")]
        last = [i for i in order if calls[i].name == "submit_advisory"]
        for i in first:
            responses[i] = self.call_tool(ids[i], calls[i].name, dict(calls[i].args or {}))
        if len(middle) > 1:
            # Announce every call before any starts, so the trace shows them running side by side.
            for i in middle:
                self.emit(ev.ToolCall(call_id=ids[i], name=calls[i].name, args=dict(calls[i].args or {})))
            with ThreadPoolExecutor(max_workers=len(middle)) as pool:
                futs = {
                    i: pool.submit(self.call_tool, ids[i], calls[i].name, dict(calls[i].args or {}), announce=False)
                    for i in middle
                }
                for i, fut in futs.items():
                    responses[i] = fut.result()
        else:
            for i in middle:
                responses[i] = self.call_tool(ids[i], calls[i].name, dict(calls[i].args or {}))
        for n, i in enumerate(last):
            if n:  # one advisory per turn
                responses[i] = {"error": "submit_advisory was already called this turn"}
                continue
            done, responses[i] = self.submit(ids[i], dict(calls[i].args or {}))
            if done:
                return True, []
        parts = []
        for i, c in enumerate(calls):
            part = types.Part.from_function_response(name=c.name, response=responses[i])
            if c.id:
                part.function_response.id = c.id
            parts.append(part)
        return False, parts

    def run(self) -> str:
        s = self.ctx.shipment
        now = self.ctx.now or s.origin.eta
        self.fs.put("forecast", self.ctx.cube.meta.label)
        self.fs.put("now", self.fs.time(now, weekday=True))
        self.emit(ev.RunStarted(
            shipment=shipment_view(self.fs), mode=self.ctx.mode, provenance=provenance(self.ctx.cube.meta),
            model=self.model,
        ))
        contents = [types.Content(role="user", parts=[types.Part(text=opening_message(s.shipment_id, self.fs.get("lane"), self.fs.get("now")))])]
        t_start = time.monotonic()
        try:
            for turn in range(1, MAX_TURNS + 1):
                self._check_cancel()
                remaining = RUN_BUDGET_S - (time.monotonic() - t_start)
                if remaining < 3:
                    raise _UseFallback(f"Gemini exceeded the {RUN_BUDGET_S:.0f} s run budget")
                self.turns = turn
                self.emit(ev.ModelTurn(turn=turn, phase="start", model=self.model))
                t0 = time.monotonic()
                try:
                    resp = self._generate(contents, min(CALL_TIMEOUT_S, remaining))
                except (genai_errors.APIError, httpx.HTTPError):
                    ms = int((time.monotonic() - t0) * 1000)
                    self.llm_ms += ms
                    self.emit(ev.ModelTurn(turn=turn, phase="end", model=self.model, latency_ms=ms))
                    raise
                ms = int((time.monotonic() - t0) * 1000)
                self.llm_ms += ms
                u = resp.usage_metadata
                used = ev.TokenUsage(
                    prompt=(u.prompt_token_count or 0) if u else 0,
                    output=(u.candidates_token_count or 0) if u else 0,
                    thoughts=(u.thoughts_token_count or 0) if u else 0,
                )
                self.tokens = ev.TokenUsage(
                    prompt=self.tokens.prompt + used.prompt, output=self.tokens.output + used.output,
                    thoughts=self.tokens.thoughts + used.thoughts,
                )
                content = resp.candidates[0].content if resp.candidates else None
                thought = " ".join(p.text.strip() for p in (content.parts or []) if p.thought and p.text) if content else ""
                self.emit(ev.ModelTurn(turn=turn, phase="end", model=self.model, latency_ms=ms, tokens=used,
                                       thought=thought or None))
                calls = list(resp.function_calls or [])
                if content is not None:
                    contents.append(content)  # keeps thought signatures for the next turn
                if not calls:
                    contents.append(types.Content(role="user", parts=[types.Part(
                        text="Respond with a tool call. Finish with submit_advisory.")]))
                    continue
                done, parts = self._dispatch(calls)
                if done:
                    return self.status
                contents.append(types.Content(role="user", parts=parts))
            raise _UseFallback(f"no valid advisory after {MAX_TURNS} turns")
        except _UseFallback as exc:
            return self.fallback(str(exc))
        except genai_errors.APIError as exc:
            return self.fallback(f"Gemini unavailable ({exc.code})")
        except httpx.TimeoutException:
            return self.fallback("Gemini did not answer in time")
        except httpx.HTTPError as exc:
            return self.fallback(f"Gemini unreachable ({type(exc).__name__})")


def run_advisory_events(ctx: RunContext, *, client: Optional[genai.Client] = None) -> str:
    """Run the agent for ctx.shipment, emitting events through ctx.emit. Returns the final status
    ('CLEAR' | 'MONITOR' | 'REROUTE' | 'HOLD', or 'ERROR' after an error event). Raises
    RunCancelled if ctx.cancel is set."""
    return _Run(ctx, client or get_client()).run()
