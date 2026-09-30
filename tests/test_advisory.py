from __future__ import annotations

from agent.advisory import (
    AdvisorySubmission,
    Findings,
    allowed_options,
    best_option,
    extract_tokens,
    fallback_advisory,
    validate,
)
from schemas import DetourAnalysis, DetourOption
from tests._synthetic import T0, line_plan, make_cube, shipment
from tools.assess import assess_route
from tools.delay import evaluate_departure_delay
from tools.facts import FactSheet, delay_payload, detour_payload, weather_payload


def _findings(cube, detour_plans=(), *, deadline_h=10.0, analysed=True, cargo="Empty 53' dry van"):
    sh = shipment(cargo=cargo, deadline_h=deadline_h)
    base = line_plan("baseline", 41.5)
    a = assess_route(base, T0, cube, sh.cargo_description)
    fs = FactSheet(sh)
    weather_payload(fs, base, a, "Scenario · synthetic forecast")
    f = Findings(sh, baseline=base, assessment=a)
    if analysed and a.verdict == "hazardous":
        cands = []
        for p in detour_plans:
            ca = assess_route(p, T0, cube, sh.cargo_description)
            cands.append(DetourOption(
                option_id=p.route_id.replace("alt:", "detour:"), plan=p, assessment=ca,
                delta_distance_meters=p.distance_meters - base.distance_meters,
                delta_duration_seconds=p.duration_seconds - base.duration_seconds,
            ))
        safe = [c for c in cands if c.assessment.verdict in ("clean", "at_risk")]
        f.detour = DetourAnalysis(
            shipment_id=sh.shipment_id, baseline_id="baseline", candidates=cands,
            chosen_id=safe[0].option_id if safe else (cands[0].option_id if cands else None), still_hazardous=not safe,
        )
        f.delay = evaluate_departure_delay(sh, base, cube)
        detour_payload(fs, f.detour)
        delay_payload(fs, f.delay)
    return f, fs


def _sub(status, option, text="Take I-70 E instead of I-80 E.", **kw):
    return AdvisorySubmission(
        status=status, chosen_option_id=option, headline=kw.get("headline", text), recommendation=text,
        rationale=kw.get("rationale", text), driver_message=kw.get("driver_message", text),
    )


I70 = line_plan("alt:i-70-e", 40.4, hours=6.3, description="I-70 E", source="alternative")


def test_allowed_statuses_follow_the_evidence():
    calm = make_cube(storm_mph=10)
    assert allowed_options(_findings(calm)[0]) == {"CLEAR": ["baseline"]}
    near = make_cube(storm_mph=32)  # 91% of 35 mph
    assert allowed_options(_findings(near)[0]) == {"MONITOR": ["baseline"]}
    storm = make_cube()
    assert allowed_options(_findings(storm, analysed=False)[0]) == {}  # must analyse detour + delay first
    # A clean on-time detour exists, and the only safe delay (+12 h) misses the deadline: reroute only.
    assert allowed_options(_findings(storm, [I70])[0]) == {"REROUTE": ["detour:i-70-e"]}
    # Deadline relaxed: both are allowed, and the earlier arrival (the detour) is the best option.
    f, _ = _findings(storm, [I70], deadline_h=30)
    assert set(allowed_options(f)) == {"REROUTE", "HOLD"} and best_option(f) == ("REROUTE", "detour:i-70-e")


def test_nothing_safe_means_open_hold_and_hazardous_detour_is_never_allowed():
    wide = make_cube(storm_box=(-108.0, 40.0, -106.0, 42.0), storm_hours=(2, 23))
    f, fs = _findings(wide, [I70])
    assert allowed_options(f) == {"HOLD": ["hold"]}
    v = validate(f, fs, _sub("REROUTE", "detour:i-70-e"))
    assert not v.ok and "not supported" in v.issues[0]


def test_grounding_rejects_invented_numbers_and_roads_but_accepts_facts():
    f, fs = _findings(make_cube(), [I70])
    k = "option.detour:i-70-e"
    good = f"Take I-70 E: {fs.get(k + '.delta_duration')}, {fs.get(k + '.cost')}; peak {fs.get('baseline.peak_wind')} on I-80 E."
    v = validate(f, fs, _sub("REROUTE", "detour:i-70-e", good))
    assert v.ok, v.issues
    assert v.grounded_numbers == len(extract_tokens(good))
    bad = validate(f, fs, _sub("REROUTE", "detour:i-70-e", "Take I-70 E; winds hit 52 mph, then use I-25 north."))
    assert not bad.ok and "52 mph" in bad.issues[0] and "I-25" in bad.issues[0]


def test_tokens_ignore_identifiers_and_normalise_units():
    assert extract_tokens("p90 on SHP-2001, 9 in 10 members") == set()
    assert extract_tokens("−32 mi (−51 km), 2 hours, 1,234 mi") == {"32 mi", "51 km", "2 h", "1234 mi"}
    assert extract_tokens("12:52–14:48 MDT, $8 est., 23°F") == {"12:52", "14:48", "$8", "23 °F"}
    assert extract_tokens("I-80 E, US-40, CO-125") == {"road I-80", "road US-40", "road STATE-125"}


def test_form_limits_option_ids_and_gusts():
    f, fs = _findings(make_cube(), [I70])
    v = validate(f, fs, _sub("REROUTE", "detour:i-70-e", headline="x" * 91, driver_message="Gusts! use detour:i-70-e"))
    joined = " | ".join(v.issues)
    assert "headline" in joined and "option ids" in joined and "gust" in joined


def test_fallback_advisory_is_valid_for_every_status():
    storm = make_cube()
    wide = make_cube(storm_box=(-108.0, 40.0, -106.0, 42.0), storm_hours=(2, 23))
    cases = [
        (_findings(make_cube(storm_mph=10)), "CLEAR"),
        (_findings(make_cube(storm_mph=32)), "MONITOR"),
        (_findings(storm, [I70]), "REROUTE"),
        (_findings(wide, [I70], deadline_h=None), "HOLD"),
        (_findings(make_cube(storm_box=(-108.0, 40.0, -106.0, 42.0)), [I70], deadline_h=30), "HOLD"),
    ]
    for (f, fs), expected in cases:
        sub = fallback_advisory(f, fs)
        v = validate(f, fs, sub)
        assert sub.status == expected and v.ok, (expected, sub, v.issues)
