"""Run the agent for one demo shipment against a replay cube and print (optionally save) its events.

    uv run python -m scripts.record_run SHP-2001                       # print the event stream
    uv run python -m scripts.record_run SHP-2001 --out data/golden/SHP-2001.jsonl

Uses real Gemini (Vertex AI, ADC) and the recorded Routes cache (no Routes API calls unless
--cache live). A saved JSONL is a golden run: the API replays it at real pacing as a labelled
"Recorded run" when live runs are capped or Vertex AI is unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent import events as ev
from agent.runner import RunContext, run_advisory_events
from data import demo_fleet_now, load_demo_fleet
from tools.cube import ForecastCube
from tools.routing import RouteCache


def _line(e) -> str:
    t = f"{e.t_ms / 1000:6.1f}s"
    match e.type:
        case "model_turn" if e.phase == "end":
            tok = e.tokens.model_dump() if e.tokens else {}
            return f"{t}  turn {e.turn} ← {e.model} {e.latency_ms} ms {tok}" + (f"\n          💭 {e.thought[:300]}" if e.thought else "")
        case "model_turn":
            return f"{t}  turn {e.turn} → {e.model}"
        case "tool_call":
            return f"{t}  ▶ {e.name} {json.dumps(e.args)[:200]}"
        case "tool_result":
            return f"{t}  {'✓' if e.ok else '✗'} {e.name} {e.latency_ms} ms · {e.summary}"
        case "guardrail":
            return f"{t}  🛡 {e.kind}: {e.message}" + "".join(f"\n          - {i}" for i in e.issues)
        case "advisory":
            return (
                f"{t}  ★ {e.status} [{e.chosen_option_id}] ({e.source}, grounded {e.grounded_numbers})\n"
                f"          H: {e.headline}\n          R: {e.recommendation}\n          W: {e.rationale}\n"
                f"          D: {e.driver_message}"
            )
        case "run_completed":
            return f"{t}  done {e.status} · {e.turns} turns · llm {e.llm_ms} ms · tools {e.tools_ms} ms · {e.tokens.model_dump()} · routes live {e.routes_live}/cached {e.routes_cached}"
        case _:
            return f"{t}  · {e.type}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("shipment_id")
    p.add_argument("--replay", type=Path, default=Path("data/replay/wn3_20260312T06Z.npz"))
    p.add_argument("--cache", choices=["live", "replay"], default="replay")
    p.add_argument("--cache-dir", type=Path, default=Path("data/routes_cache"))
    p.add_argument("--out", type=Path, default=None, help="Write the events as JSONL (a golden run).")
    args = p.parse_args(argv)

    cube = ForecastCube.load_npz(args.replay)
    now = demo_fleet_now(cube.meta.init_time)
    fleet = {s.shipment_id: s for s in load_demo_fleet(reference_time=now)}
    events: list = []

    def sink(e) -> None:
        events.append(e)
        print(_line(e), flush=True)

    ctx = RunContext(
        shipment=fleet[args.shipment_id], cube=cube, routes=RouteCache(args.cache_dir, mode=args.cache).scoped(),
        emit=ev.Emitter(sink), mode=cube.meta.source, now=now,
    )
    status = run_advisory_events(ctx)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("".join(e.model_dump_json(exclude_none=True) + "\n" for e in events))
        print(f"wrote {len(events)} events → {args.out}")
    return 0 if status != "ERROR" else 1


if __name__ == "__main__":
    sys.exit(main())
