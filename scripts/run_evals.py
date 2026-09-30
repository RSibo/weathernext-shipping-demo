"""Live agent evals: run every case in evals/cases.jsonl N times with real Gemini and score them.

    uv run python -m scripts.run_evals                 # 3 runs per case, 5 in parallel
    uv run python -m scripts.run_evals --runs 1 --only hero-reroute

Every check has a ground truth, so there is no LLM judge here: the decision, the safety of the
chosen option (read back from the run's own detour/delay events, independently of the guardrails
that produced it), template fallbacks and latency. Routes come from the recorded cache, so the only
live calls are to Gemini. Results go to evals/artifacts/<timestamp>.json.

Pass bar (exit 1 if missed): 100% safe, >= 90% expected decision, <= 10% template fallbacks, p95 < 30 s.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from agent import events as ev
from agent.runner import RunContext, run_advisory_events
from data import demo_fleet_now, load_demo_fleet
from tools.cube import ForecastCube
from tools.routing import RouteCache

REPO = Path(__file__).resolve().parent.parent
SAFE = ("clean", "at_risk")


def safety_issues(events: list) -> list[str]:
    """Why the final advisory is unsafe given this run's own evidence (empty = safe)."""
    by_type = {e.type: e for e in events}  # last of each type
    adv, weather = by_type.get("advisory"), by_type.get("weather_checked")
    if adv is None or weather is None:
        return ["no advisory"]
    baseline = weather.assessment.verdict
    issues = []
    if baseline == "hazardous" and adv.status in ("CLEAR", "MONITOR"):
        issues.append(f"{adv.status} on a route that breaches")
    if adv.status == "CLEAR" and baseline != "clean":
        issues.append(f"CLEAR on a {baseline} route")
    if adv.status == "REROUTE":
        detour = by_type.get("detour_computed")
        chosen = next((c for c in detour.candidates if c.option_id == adv.chosen_option_id), None) if detour else None
        if chosen is None or chosen.assessment.verdict not in SAFE:
            issues.append(f"REROUTE onto {adv.chosen_option_id} ({chosen.assessment.verdict if chosen else 'unknown'})")
    if adv.status == "HOLD" and adv.chosen_option_id != "hold":
        delay = by_type.get("delay_evaluated")
        chosen = next((o for o in delay.options if o.option_id == adv.chosen_option_id), None) if delay else None
        if chosen is None or chosen.verdict not in SAFE:
            issues.append(f"HOLD to {adv.chosen_option_id} ({chosen.verdict if chosen else 'unknown'})")
    return issues


def score(case: dict, events: list, wall_ms: int) -> dict:
    adv = next((e for e in reversed(events) if e.type == "advisory"), None)
    done = next((e for e in reversed(events) if e.type == "run_completed"), None)
    return {
        "case": case["id"],
        "status": adv.status if adv else "ERROR",
        "option": adv.chosen_option_id if adv else None,
        "expected": adv is not None and adv.status == case["expect_status"] and adv.chosen_option_id == case["expect_option"],
        "safety_issues": safety_issues(events),
        "source": adv.source if adv else None,
        "rejections": sum(1 for e in events if e.type == "guardrail" and e.kind == "advisory_rejected"),
        "rejection_issues": [i for e in events if e.type == "guardrail" and e.kind == "advisory_rejected" for i in e.issues],
        "grounded_numbers": adv.grounded_numbers if adv else 0,
        "turns": done.turns if done else None,
        "total_ms": done.total_ms if done else wall_ms,
        "headline": adv.headline if adv else None,
    }


def p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", type=Path, default=REPO / "evals" / "cases.jsonl")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--parallel", type=int, default=5)
    p.add_argument("--only", action="append", default=[], help="Case id (repeatable).")
    p.add_argument("--replay", type=Path, default=REPO / "data" / "replay" / "wn3_20260312T06Z.npz")
    p.add_argument("--cache-dir", type=Path, default=REPO / "data" / "routes_cache")
    p.add_argument("--out-dir", type=Path, default=REPO / "evals" / "artifacts")
    args = p.parse_args(argv)

    cases = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    cube = ForecastCube.load_npz(args.replay)
    now = demo_fleet_now(cube.meta.init_time)
    fleet = {s.shipment_id: s for s in load_demo_fleet(reference_time=now)}
    routes = RouteCache(args.cache_dir, mode="replay")

    def run_one(case: dict) -> dict:
        events: list = []
        emitter = ev.Emitter(events.append)
        ctx = RunContext(shipment=fleet[case["shipment_id"]], cube=cube, routes=routes.scoped(), emit=emitter,
                         mode=cube.meta.source, now=now)
        run_advisory_events(ctx)
        return score(case, events, emitter.elapsed_ms())

    jobs = [c for c in cases for _ in range(args.runs)]
    print(f"{len(jobs)} runs ({len(cases)} cases x {args.runs}), {args.parallel} in parallel", flush=True)
    with ThreadPoolExecutor(args.parallel) as pool:
        results = list(pool.map(run_one, jobs))

    for r in results:
        mark = "ok " if r["expected"] and not r["safety_issues"] else "BAD"
        print(f"{mark} {r['case']:20} {r['status']:8} {r['option'] or '-':16} {r['source'] or '-':8} "
              f"rej={r['rejections']} {r['total_ms'] / 1000:5.1f}s  {r['headline'] or ''}"
              + "".join(f"\n      unsafe: {i}" for i in r["safety_issues"])
              + "".join(f"\n      rejected: {i}" for i in r["rejection_issues"]))

    n = len(results)
    summary = {
        "runs": n,
        "safe": sum(not r["safety_issues"] for r in results) / n,
        "expected": sum(r["expected"] for r in results) / n,
        "fallback": sum(r["source"] != "gemini" for r in results) / n,
        "rejected_once": sum(r["rejections"] > 0 for r in results) / n,
        "p50_s": sorted(r["total_ms"] for r in results)[n // 2] / 1000,
        "p95_s": p95([r["total_ms"] for r in results]) / 1000,
    }
    passed = summary["safe"] == 1 and summary["expected"] >= 0.9 and summary["fallback"] <= 0.1 and summary["p95_s"] < 30
    print(f"\nsafe {summary['safe']:.0%} · expected decision {summary['expected']:.0%} · template fallback "
          f"{summary['fallback']:.0%} · rejected once {summary['rejected_once']:.0%} · p50 {summary['p50_s']:.1f}s "
          f"· p95 {summary['p95_s']:.1f}s → {'PASS' if passed else 'FAIL'}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps({"summary": summary, "passed": passed, "results": results}, indent=2))
    print(f"wrote {out.relative_to(REPO) if out.is_relative_to(REPO) else out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
