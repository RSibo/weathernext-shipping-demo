"""Capture one WeatherNext 3 forecast init over a bbox into a replay cube (.npz).

    uv run python -m scripts.capture_replay --init 2026-03-12T06:00:00Z --dry-run
    uv run python -m scripts.capture_replay --init 2026-03-12T06:00:00Z --out data/replay/hero.npz

Cost model (measured with dry-runs, 2026-09-30): the view is partitioned by DAY on init_time, so
any single-init query is billed as that whole day, per array column read (~126 GB each for
wind/precip/temp/hours) plus ~6 GB for `geography`. The bbox size does not change the bytes — a
spatial predicate filters rows, it does not prune storage. So capture one wide bbox covering
every demo lane in a single query instead of one query per lane. The dry-run estimate is always
checked first and `maximum_bytes_billed` is pinned to it, so a surprise can only fail for free.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from google.cloud import bigquery

import config
from tools.units import kelvin_to_fahrenheit, meters_to_mm, ms_to_mph

# Covers every demo lane: SLC->Denver (I-80 and I-70), Rock Springs->Cheyenne, Phoenix->LA,
# Houston->Dallas->OKC. (west, south, east, north)
DEFAULT_BBOX = (-119.0, 29.0, -94.0, 44.0)
GRID_DEG = 0.1
PRICE_PER_TB_USD = 6.25


def _query(init: datetime, bbox: tuple[float, float, float, float], max_hours: int) -> str:
    w, s, e, n = bbox
    lo = init.strftime("%Y-%m-%d %H:%M:%S")
    hi = (init + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    table = f"`{config.PROJECT_ID}.{config.WEATHERNEXT_DATASET}.{config.WEATHERNEXT_TABLE}`"
    return f"""
SELECT
  ST_Y(t.geography) AS lat,
  ST_X(t.geography) AS lon,
  ARRAY_AGG(STRUCT(f.hours AS h, f.wind_speed_10m_p90 AS w, f.imerg_tp_1hr_p90 AS p,
                   f.temperature_2m_mean AS k) ORDER BY f.hours) AS fc
FROM {table} t, UNNEST(t.forecast) f
WHERE t.init_time >= TIMESTAMP '{lo}+00' AND t.init_time < TIMESTAMP '{hi}+00'
  AND ST_INTERSECTS(t.geography, ST_GEOGFROMTEXT(
        'POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))'))
  AND f.hours BETWEEN 0 AND {max_hours}
GROUP BY lat, lon
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--init", required=True, help="Forecast init_time, e.g. 2026-03-12T06:00:00Z")
    ap.add_argument("--bbox", type=float, nargs=4, default=DEFAULT_BBOX, metavar=("W", "S", "E", "N"))
    ap.add_argument("--max-hours", type=int, default=48)
    ap.add_argument("--max-gb", type=float, default=600.0, help="Refuse to run above this estimate")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    init = datetime.fromisoformat(args.init.replace("Z", "+00:00")).astimezone(timezone.utc)
    client = bigquery.Client(project=config.PROJECT_ID, location=config.BQ_LOCATION)
    sql = _query(init, tuple(args.bbox), args.max_hours)

    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    est = dry.total_bytes_processed
    print(f"dry-run: {est / 1e9:.1f} GB (~${est / 1e12 * PRICE_PER_TB_USD:.2f})")
    if args.dry_run:
        return 0
    if est > args.max_gb * 1e9:
        print(f"refusing: estimate exceeds --max-gb {args.max_gb}", file=sys.stderr)
        return 2
    if args.out is None:
        print("--out is required unless --dry-run", file=sys.stderr)
        return 2

    # Billing rounds up to whole MB per table read, so the cap needs a hair of headroom over the
    # dry-run figure (observed: +0.9 MB on a 505 GB estimate).
    cap = est + 100 * 1024**2
    job = client.query(
        sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=cap, use_query_cache=False)
    )
    rows = list(job.result())
    billed = job.total_bytes_billed or 0
    print(f"billed: {billed / 1e9:.1f} GB (~${billed / 1e12 * PRICE_PER_TB_USD:.2f}), {len(rows)} cells")
    if not rows:
        print("no rows: init_time not in the archive or bbox empty", file=sys.stderr)
        return 1

    w, s, e, n = args.bbox
    lats = np.round(np.arange(s, n + GRID_DEG / 2, GRID_DEG), 4)
    lons = np.round(np.arange(w, e + GRID_DEG / 2, GRID_DEG), 4)
    hours = sorted({item["h"] for r in rows for item in r["fc"]})
    h_index = {h: i for i, h in enumerate(hours)}
    shape = (len(hours), len(lats), len(lons))
    wind = np.full(shape, np.nan, dtype=np.float32)
    precip = np.full(shape, np.nan, dtype=np.float32)
    temp = np.full(shape, np.nan, dtype=np.float32)
    off_grid = 0
    for r in rows:
        yi = int(round((r["lat"] - s) / GRID_DEG))
        xi = int(round((r["lon"] - w) / GRID_DEG))
        if not (0 <= yi < len(lats) and 0 <= xi < len(lons)) or abs(lats[yi] - r["lat"]) > 0.02:
            off_grid += 1
            continue
        for item in r["fc"]:
            ti = h_index[item["h"]]
            if item["w"] is not None:
                wind[ti, yi, xi] = ms_to_mph(item["w"])
            if item["p"] is not None:
                precip[ti, yi, xi] = max(0.0, meters_to_mm(item["p"]))
            if item["k"] is not None:
                temp[ti, yi, xi] = kelvin_to_fahrenheit(item["k"])

    valid_s = np.array([int((init + timedelta(hours=h)).timestamp()) for h in hours], dtype=np.int64)
    meta = {
        "source": "replay",
        "dataset": f"{config.WEATHERNEXT_DATASET}.{config.WEATHERNEXT_TABLE}",
        "init_time": init.isoformat().replace("+00:00", "Z"),
        "bbox": list(args.bbox),
        "grid_deg": GRID_DEG,
        "variables": {
            "wind_mph": "wind_speed_10m_p90 (sustained 10 m wind, ensemble p90), mph",
            "precip_mm_hr": "imerg_tp_1hr_p90 (IMERG-calibrated 1 h precip, ensemble p90), mm/hr",
            "temp_f": "temperature_2m_mean (ensemble mean), degF",
        },
        "bytes_billed": billed,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cells": len(rows),
        "off_grid_cells": off_grid,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out, lat=lats, lon=lons, valid_time_s=valid_s, wind_mph=wind,
        precip_mm_hr=precip, temp_f=temp, meta=np.array(json.dumps(meta)),
    )
    filled = np.isfinite(wind).mean()
    print(f"wrote {args.out} shape={shape} filled={filled:.1%} off_grid={off_grid}")
    print(f"peak p90 wind in bbox: {np.nanmax(wind):.1f} mph")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
