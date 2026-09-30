"""Process-wide state for the web API: forecast scenes, the Routes cache, golden runs and run limits.

Built once at startup (api/main.py lifespan) and read by every request. Nothing here is per-user:
the public URL has no accounts, so the only per-request state is the run itself.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config
from agent import events as ev
from data import demo_fleet_now, load_demo_fleet
from schemas import Shipment
from tools.cube import CubeSource, ForecastCube
from tools.routing import RouteCache

log = logging.getLogger(__name__)


@dataclass
class Scene:
    """One forecast plus the demo fleet as of that forecast's story time."""

    mode: CubeSource
    cube: ForecastCube
    now: datetime
    shipments: dict[str, Shipment]
    fleet: Optional[Any] = None  # api.fleet.Fleet, computed once per scene
    fleet_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_cube(cls, cube: ForecastCube) -> "Scene":
        now = demo_fleet_now(cube.meta.init_time) if cube.meta.source != "live" else datetime.now(timezone.utc)
        return cls(
            mode=cube.meta.source, cube=cube, now=now,
            shipments={s.shipment_id: s for s in load_demo_fleet(reference_time=now)},
        )


class RunLimiter:
    """Concurrent and daily caps on live Gemini runs (per instance; Cloud Run max-instances bounds the total)."""

    def __init__(self, max_concurrent: int, daily_cap: int):
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self.daily_cap = daily_cap
        self._day: Optional[date] = None
        self.today = 0

    def try_acquire(self) -> Optional[str]:
        """None if the run may start (call release() when it ends), else the reason it may not."""
        with self._lock:
            day = datetime.now(timezone.utc).date()
            if day != self._day:
                self._day, self.today = day, 0
            if self.today >= self.daily_cap:
                return "Today's live runs are used up"
            if not self._sem.acquire(blocking=False):
                return "Live runs are busy right now"
            self.today += 1
            return None

    def release(self) -> None:
        self._sem.release()


def load_golden(directory: Path) -> dict[str, list[ev.RunEvent]]:
    """Recorded runs (scripts/record_run.py --out) keyed by shipment id. Invalid files are skipped, not fatal."""
    runs: dict[str, list[ev.RunEvent]] = {}
    for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else []:
        try:
            events = [ev.parse_event(line) for line in path.read_text().splitlines() if line.strip()]
        except ValueError as exc:
            log.warning("skipping golden run %s: %s", path.name, exc)
            continue
        if events and events[0].type == "run_started" and events[-1].type == "run_completed":
            runs[events[0].shipment.shipment_id] = events
        else:
            log.warning("skipping golden run %s: not a complete run", path.name)
    return runs


@dataclass
class AppState:
    scenes: dict[str, Scene]
    routes: RouteCache
    golden: dict[str, list[ev.RunEvent]]
    limiter: RunLimiter
    default_mode: str

    def scene(self, mode: Optional[str]) -> Optional[Scene]:
        return self.scenes.get(mode or self.default_mode)


def build_state() -> AppState:
    scenes: dict[str, Scene] = {}
    replay = Path(config.REPLAY_CUBE_PATH)
    if replay.exists():
        scenes["replay"] = Scene.from_cube(ForecastCube.load_npz(replay))
    else:
        log.warning("replay cube %s not found; replay mode disabled", replay)
    golden = load_golden(Path(config.GOLDEN_DIR))
    log.info("scenes=%s golden=%s routes_cache=%s", list(scenes), sorted(golden), config.ROUTES_CACHE_MODE)
    return AppState(
        scenes=scenes,
        routes=RouteCache(Path(config.ROUTES_CACHE_DIR), mode=config.ROUTES_CACHE_MODE),
        golden=golden,
        limiter=RunLimiter(config.MAX_CONCURRENT_RUNS, config.DAILY_LIVE_RUN_CAP),
        default_mode="replay" if "replay" in scenes else next(iter(scenes), "replay"),
    )
