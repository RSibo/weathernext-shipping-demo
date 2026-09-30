"""The dispatch loop against a scripted fake Gemini: event order, guardrails, retries, fallbacks."""

from __future__ import annotations

import httpx
import pytest
from google.genai import errors as genai_errors

import agent.runner as runner
import config
import tools.detour as detour
from agent import events as ev
from tests._fake_gemini import GOOD, HERO, FakeClient, call, resp
from tests._synthetic import line_plan, make_cube, shipment
from tools.routing import RouteCache


@pytest.fixture
def lane(monkeypatch):
    """Storm on I-80 (lat 41.5); I-70 (lat 40.4) is clean. No stops, so detours come from alternatives."""
    base = line_plan("baseline", 41.5)
    i70 = line_plan("alt:i-70-e", 40.4, hours=6.3, description="I-70 E", source="alternative")

    def routes(origin, destination, *, alternatives=False, **kw):
        return [base, i70] if alternatives else [base]

    monkeypatch.setattr(runner, "compute_routes", routes)
    monkeypatch.setattr(detour, "compute_routes", routes)
    monkeypatch.setattr(detour, "load_anchors", lambda: ())
    return make_cube()


def run(cube, script, sh=None, **ctx_kw):
    out: list = []
    client = FakeClient(script)
    ctx = runner.RunContext(
        shipment=sh or shipment(), cube=cube, routes=RouteCache(None, mode="live").scoped(),
        emit=ev.Emitter(out.append), mode="scenario", **ctx_kw,
    )
    status = runner.run_advisory_events(ctx, client=client)
    return status, out, client


def types_of(out):
    return [e.type for e in out]


def of(out, t):
    return [e for e in out if e.type == t]


def test_hero_reroute_streams_a_complete_well_formed_run(lane):
    status, out, client = run(lane, HERO)
    assert status == "REROUTE"
    t = types_of(out)
    assert t[0] == "run_started" and t[-2:] == ["advisory", "run_completed"]
    assert t.index("route_planned") < t.index("weather_checked") < t.index("detour_computed")
    # Both parallel calls are announced before either result arrives.
    assert t[t.index("weather_checked") + 4 : t.index("weather_checked") + 6] == ["tool_call", "tool_call"]
    assert [e.seq for e in out] == list(range(1, len(out) + 1))
    # Every tool_result answers an earlier tool_call.
    seen = set()
    for e in out:
        if e.type == "tool_call":
            seen.add(e.call_id)
        if e.type == "tool_result":
            assert e.call_id in seen
    adv = of(out, "advisory")[0]
    assert adv.source == "gemini" and adv.chosen_option_id == "detour:i-70-e"
    assert [m.key for m in adv.metrics][:3] == ["delta_duration", "delta_distance", "cost"]
    assert of(out, "model_turn")[1].thought == "Check the weather first."
    assert of(out, "detour_computed")[0].chosen_id == "detour:i-70-e"
    done = out[-1]
    assert done.turns == 3 and done.tokens.prompt == 300
    # Function responses echo the call ids, and every event survives the wire union.
    last_user = client.requests[2]["contents"][-1]
    assert [p.function_response.id for p in last_user.parts] == ["c0", "c1"]
    for e in out:
        assert ev.parse_event(e.model_dump_json()).type == e.type


def test_gemini_config_is_gemini_3_style(lane):
    _, _, client = run(lane, HERO)
    cfg = client.requests[0]["config"]
    assert cfg.temperature == 1.0
    assert cfg.thinking_config.thinking_level is not None and cfg.thinking_config.thinking_budget is None
    assert cfg.tool_config.function_calling_config.mode == "ANY"
    assert cfg.automatic_function_calling.disable is True


def test_ungrounded_advisory_is_returned_once_then_accepted(lane):
    bad = {**GOOD, "headline": "Reroute via I-70 E: 52 mph winds on I-80 E"}
    script = HERO[:2] + [resp(call("submit_advisory", **bad)), HERO[2]]
    status, out, client = run(lane, script)
    kinds = [g.kind for g in of(out, "guardrail")]
    assert kinds == ["advisory_rejected", "advisory_accepted"]
    assert "52 mph" in of(out, "guardrail")[0].issues[0]
    assert status == "REROUTE" and of(out, "advisory")[0].source == "gemini"
    # The rejection went back to Gemini as the function response.
    fr = client.requests[3]["contents"][-1].parts[0].function_response
    assert "issues" in fr.response


def test_two_rejections_fall_back_to_the_template(lane):
    wrong = {**GOOD, "status": "CLEAR", "chosen_option_id": "baseline"}
    script = HERO[:2] + [resp(call("submit_advisory", **wrong))] * 2
    status, out, _ = run(lane, script)
    assert "status CLEAR is not supported" in of(out, "guardrail")[0].issues[0]
    assert of(out, "guardrail")[-1].kind == "fallback_used"
    adv = of(out, "advisory")[0]
    assert status == "REROUTE" and adv.source == "fallback" and adv.chosen_option_id == "detour:i-70-e"


def test_detour_before_weather_is_blocked_and_the_run_recovers(lane):
    script = [resp(call("compute_detour_route")), *HERO]
    status, out, _ = run(lane, script)
    blocked = of(out, "guardrail")[0]
    assert blocked.kind == "tool_blocked" and "check_route_weather" in blocked.message
    assert of(out, "tool_result")[0].ok is False
    assert status == "REROUTE"


def test_wrong_shipment_id_is_blocked(lane):
    script = [resp(("check_route_weather", {"shipment_id": "SHP-9999"})), *HERO]
    _, out, _ = run(lane, script)
    assert "SHP-9999" in of(out, "guardrail")[0].message


def test_advising_before_the_analysis_is_complete_is_rejected(lane):
    script = [HERO[0], HERO[2], HERO[1], HERO[2]]
    status, out, _ = run(lane, script)
    assert "compute_detour_route and evaluate_departure_delay" in of(out, "guardrail")[0].issues[0]
    assert status == "REROUTE" and of(out, "advisory")[0].source == "gemini"


def test_vertex_outage_completes_the_analysis_and_uses_the_template(lane):
    status, out, _ = run(lane, [genai_errors.ServerError(503, {"error": {"code": 503, "message": "down"}})])
    assert status == "REROUTE"
    calls = [e.name for e in of(out, "tool_call")]
    assert calls == ["check_route_weather", "compute_detour_route", "evaluate_departure_delay"]
    assert all(e.call_id.startswith("sys-") for e in of(out, "tool_call"))
    turns = of(out, "model_turn")
    assert [m.phase for m in turns] == ["start", "end"]  # the failed turn is closed, no dangling spinner
    assert of(out, "advisory")[0].source == "fallback"


def test_timeout_falls_back(lane):
    status, out, _ = run(lane, [HERO[0], httpx.ReadTimeout("slow")])
    assert status == "REROUTE" and "in time" in of(out, "guardrail")[-1].message


def test_turn_exhaustion_falls_back(lane):
    status, out, client = run(lane, [resp(call("check_route_weather"))] * runner.MAX_TURNS)
    assert len(client.requests) == runner.MAX_TURNS
    assert of(out, "advisory")[0].source == "fallback" and status == "REROUTE"


def test_text_only_turn_is_nudged_back_to_tools(lane):
    _, out, client = run(lane, [resp(text="Let me think."), *HERO])
    assert "submit_advisory" in client.requests[1]["contents"][-1].parts[0].text
    assert of(out, "advisory")[0].source == "gemini"


def test_missing_model_switches_to_the_fallback_model(lane):
    nf = genai_errors.ClientError(404, {"error": {"code": 404, "message": "not found"}})
    status, out, client = run(lane, [nf, *HERO])
    assert client.requests[1]["model"] == config.GEMINI_FALLBACK_MODEL
    cfg = client.requests[1]["config"].thinking_config
    assert cfg.thinking_budget is not None and cfg.thinking_level is None
    assert out[-1].model == config.GEMINI_FALLBACK_MODEL and status == "REROUTE"


def test_clean_route_clears_and_blocks_detours(monkeypatch):
    base = line_plan("baseline", 41.5)
    monkeypatch.setattr(runner, "compute_routes", lambda *a, **k: [base])
    clear = {**GOOD, "status": "CLEAR", "chosen_option_id": "baseline", "headline": "Clear to run I-80 E",
             "recommendation": "Run I-80 E as planned.", "rationale": "Winds stay under the 35 mph limit.",
             "driver_message": "You're clear to run I-80 E."}
    script = [HERO[0], resp(call("compute_detour_route")), resp(call("submit_advisory", **clear))]
    status, out, _ = run(make_cube(storm_mph=10), script)
    assert status == "CLEAR"
    assert "only for hazardous routes" in of(out, "guardrail")[0].message


def test_cancel_stops_the_run(lane):
    import threading

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(runner.RunCancelled):
        run(lane, HERO, cancel=cancel)


def test_sse_frame_and_emitter():
    out: list = []
    emit = ev.Emitter(out.append, run_id="r1")
    emit(ev.Guardrail(kind="tool_blocked", message="x"))
    frame = ev.to_sse(out[0])
    assert frame.startswith("data: {") and frame.endswith("\n\n") and '"run_id":"r1"' in frame and '"seq":1' in frame
