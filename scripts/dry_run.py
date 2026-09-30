"""Run the deterministic pipeline (no LLM) for one or all demo shipments and print what it finds.

    uv run python -m scripts.dry_run --shipment SHP-2001
    uv run python -m scripts.dry_run --all --cache replay     # no Routes API calls at all

This is the Day-1 check that the hero story exists in the data: the baseline breaches, a detour
that is actually different is verified clean at its own ETAs, and delay options are scored.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from data import demo_fleet_now, load_demo_fleet
from schemas import RouteAssessment
from tools.assess import assess_route
from tools.cube import ForecastCube
from tools.delay import evaluate_departure_delay
from tools.detour import find_detour
from tools.routing import RouteCache, compute_routes

DEFAULT_REPLAY = Path("data/replay/wn3_20260312T06Z.npz")
DEFAULT_CACHE_DIR = Path("data/routes_cache")


def _fmt_seg(a: RouteAssessment, tz: ZoneInfo) -> list[str]:
    out = []
    for s in [*a.segments, *a.near_segments]:
        out.append(
            f"    {s.kind:6} km {s.start_km:6.1f}-{s.end_km:6.1f}  "
            f"{s.start_eta.astimezone(tz):%H:%M}-{s.end_eta.astimezone(tz):%H:%M}  "
            f"peak {s.peak_wind_mph:4.1f} mph  precip {s.peak_precip_mm_hr:4.1f}  min {s.min_temp_f:5.1f}F  "
            f"near {s.near_place}  {s.hazards}"
        )
    return out


def _summary(a: RouteAssessment) -> str:
    return (
        f"{a.verdict:11} peak {a.peak_wind_mph} mph @ km {a.peak_wind_km}  thr {a.wind_threshold_mph}  "
        f"samples {len(a.samples)} @ {a.sample_spacing_km} km  unmonitored {a.unmonitored_fraction:.1%}  "
        f"exposure {a.exposure_km} km / {a.exposure_score}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--shipment", action="append", default=[])
    p.add_argument("--all", action="store_true")
    p.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    p.add_argument("--cache", choices=["live", "replay"], default="live")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    p.add_argument("--now-offset", type=float, default=None, help="Override hours after init for 'now'.")
    args = p.parse_args(argv)

    cube = ForecastCube.load_npz(args.replay)
    now = demo_fleet_now(cube.meta.init_time)
    if args.now_offset is not None:
        from datetime import timedelta

        now = cube.meta.init_time + timedelta(hours=args.now_offset)
    cache = RouteCache(args.cache_dir, mode=args.cache)
    fleet = {s.shipment_id: s for s in load_demo_fleet(reference_time=now)}
    ids = list(fleet) if args.all or not args.shipment else args.shipment

    print(f"{cube.meta.label}  ·  now = {now:%Y-%m-%d %H:%MZ}  ·  horizon {cube.first_time:%d %H:%MZ} → {cube.last_time:%d %H:%MZ}")
    for sid in ids:
        sh = fleet[sid]
        tz = ZoneInfo(sh.timezone)
        stops = [w.location for w in sh.waypoints]
        baseline = compute_routes(sh.origin.location, sh.destination.location, stops=stops, cache=cache)[0]
        a = assess_route(baseline, sh.origin.eta, cube, sh.cargo_description)
        print(f"\n== {sid}  {sh.origin.name} → {sh.destination.name}  ·  {sh.cargo_description}")
        print(f"  depart {sh.origin.eta.astimezone(tz):%a %H:%M %Z}  deadline {sh.delivery_deadline.astimezone(tz):%H:%M}" if sh.delivery_deadline else "")
        print(f"  baseline {baseline.description!r}  {baseline.distance_meters / 1609.344:.0f} mi  {baseline.duration_seconds / 3600:.2f} h")
        print(f"  → {_summary(a)}")
        print("\n".join(_fmt_seg(a, tz)))
        if a.verdict != "hazardous":
            continue
        d = find_detour(sh, baseline, a, cube, cache=cache)
        print(f"  detour: {len(d.candidates)} candidates, anchors tried {d.anchors_tried}, routes calls {d.routes_calls}")
        for c in d.candidates:
            mark = "★" if c.option_id == d.chosen_id else " "
            print(
                f"   {mark} {c.option_id:28} {c.plan.description!r:22} "
                f"+{c.delta_distance_meters / 1609.344:5.1f} mi  +{c.delta_duration_seconds / 60:4.0f} min  → {_summary(c.assessment)}"
            )
            if c.assessment.verdict != "clean":
                print("\n".join("     " + x for x in _fmt_seg(c.assessment, tz)))
        print(f"   chosen={d.chosen_id} still_hazardous={d.still_hazardous}")
        dl = evaluate_departure_delay(sh, baseline, cube)
        for o in dl.options:
            print(
                f"   {o.option_id:10} depart {o.depart_at.astimezone(tz):%H:%M} arrive {o.arrive_at.astimezone(tz):%H:%M}  "
                f"{o.verdict:15} peak {o.peak_wind_mph}  meets_deadline={o.meets_deadline}"
            )
        print(f"   earliest_safe={dl.earliest_safe_id}")
    print(f"\nroutes: live {cache.live_calls} · cached {cache.cached_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
