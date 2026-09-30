"""ForecastCube: one WeatherNext 3 forecast init as dense [T, Y, X] arrays on the 0.1deg grid.

Every consumer (route assessment, detour candidates, departure delays, the fleet scan, the browser
forecast layer) reads the same cube, so they cannot disagree about what the forecast says. The
cube is built once per init (live BigQuery refresh, a captured replay, or a synthetic scenario)
and then queried in memory — no per-click BigQuery cost.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import numpy as np

CubeSource = Literal["live", "replay", "scenario"]

# Browser wire format: uint8, 0 = no data, value = offset + (q - 1) * step. Physical steps, not
# 254 levels: 1 mph is below anything a viewer can see and halves the gzipped payload.
WIRE_STEPS = {"wind_mph": (0.0, 1.0), "precip_mm_hr": (0.0, 0.25), "temp_f": (-40.0, 1.0)}


@dataclass(frozen=True)
class CubeMeta:
    source: CubeSource
    init_time: datetime
    dataset: str = ""
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    grid_deg: float = 0.1
    bytes_billed: int = 0
    captured_at: Optional[datetime] = None
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        issued = self.init_time.strftime("%b %-d %H:%MZ")
        return {
            "live": f"Live · WeatherNext 3 forecast issued {issued}",
            "replay": f"Replay · WeatherNext 3 forecast issued {issued}",
            "scenario": "Scenario · synthetic forecast",
        }[self.source]


@dataclass(frozen=True)
class CellHour:
    t: datetime
    wind_mph: float
    precip_mm_hr: float
    temp_f: float


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


class ForecastCube:
    def __init__(
        self,
        meta: CubeMeta,
        lat: np.ndarray,
        lon: np.ndarray,
        times_s: np.ndarray,
        wind_mph: np.ndarray,
        precip_mm_hr: np.ndarray,
        temp_f: np.ndarray,
    ):
        shape = (len(times_s), len(lat), len(lon))
        for name, arr in (("wind_mph", wind_mph), ("precip_mm_hr", precip_mm_hr), ("temp_f", temp_f)):
            if arr.shape != shape:
                raise ValueError(f"{name} shape {arr.shape} != {shape}")
        if len(lat) < 2 or len(lon) < 2 or len(times_s) < 1:
            raise ValueError("cube needs >= 2 lats, >= 2 lons, >= 1 time")
        self.meta = meta
        self.lat = np.asarray(lat, dtype=np.float64)
        self.lon = np.asarray(lon, dtype=np.float64)
        self.times_s = np.asarray(times_s, dtype=np.int64)
        self.wind_mph = wind_mph.astype(np.float32, copy=False)
        self.precip_mm_hr = precip_mm_hr.astype(np.float32, copy=False)
        self.temp_f = temp_f.astype(np.float32, copy=False)
        self._dlat = float(self.lat[1] - self.lat[0])
        self._dlon = float(self.lon[1] - self.lon[0])

    # --- I/O ------------------------------------------------------------------------------
    @classmethod
    def load_npz(cls, path: str | Path, *, source: Optional[CubeSource] = None) -> "ForecastCube":
        z = np.load(path, allow_pickle=False)
        raw = json.loads(str(z["meta"]))
        meta = CubeMeta(
            source=source or raw.get("source", "replay"),
            init_time=_parse_ts(raw["init_time"]),
            dataset=raw.get("dataset", ""),
            bbox=tuple(raw.get("bbox", (0, 0, 0, 0))),
            grid_deg=float(raw.get("grid_deg", 0.1)),
            bytes_billed=int(raw.get("bytes_billed", 0)),
            captured_at=_parse_ts(raw["captured_at"]) if raw.get("captured_at") else None,
            extra={k: v for k, v in raw.items() if k not in {"source", "init_time", "dataset", "bbox", "grid_deg", "bytes_billed", "captured_at"}},
        )
        return cls(meta, z["lat"], z["lon"], z["valid_time_s"], z["wind_mph"], z["precip_mm_hr"], z["temp_f"])

    def save_npz(self, path: str | Path) -> None:
        m = self.meta
        meta = {
            "source": m.source,
            "init_time": m.init_time.isoformat().replace("+00:00", "Z"),
            "dataset": m.dataset,
            "bbox": list(m.bbox),
            "grid_deg": m.grid_deg,
            "bytes_billed": m.bytes_billed,
            "captured_at": m.captured_at.isoformat().replace("+00:00", "Z") if m.captured_at else None,
            **m.extra,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path, lat=self.lat, lon=self.lon, valid_time_s=self.times_s, wind_mph=self.wind_mph,
            precip_mm_hr=self.precip_mm_hr, temp_f=self.temp_f, meta=np.array(json.dumps(meta)),
        )

    # --- Queries --------------------------------------------------------------------------
    @property
    def first_time(self) -> datetime:
        return datetime.fromtimestamp(int(self.times_s[0]), tz=timezone.utc)

    @property
    def last_time(self) -> datetime:
        return datetime.fromtimestamp(int(self.times_s[-1]), tz=timezone.utc)

    def cell_index(self, lat: float, lon: float) -> Optional[tuple[int, int]]:
        yi = int(round((lat - self.lat[0]) / self._dlat))
        xi = int(round((lon - self.lon[0]) / self._dlon))
        if 0 <= yi < len(self.lat) and 0 <= xi < len(self.lon):
            return yi, xi
        return None

    def cell_bounds(self, yi: int, xi: int) -> tuple[float, float, float, float]:
        """(west, south, east, north) of a cell."""
        la, lo = self.lat[yi], self.lon[xi]
        return (lo - self._dlon / 2, la - self._dlat / 2, lo + self._dlon / 2, la + self._dlat / 2)

    def covers_time(self, t: datetime) -> bool:
        ts = t.timestamp()
        return self.times_s[0] - 1800 <= ts <= self.times_s[-1] + 1800

    def window(self, lat: float, lon: float, t: datetime, window_h: float = 1.0) -> Optional[list[CellHour]]:
        """Every forecast hour within +/- window_h of t at the cell containing (lat, lon).

        None means UNMONITORED — outside the bbox, past the forecast horizon, or no data in the
        cell. Callers must never treat None as clean.
        """
        idx = self.cell_index(lat, lon)
        if idx is None or not self.covers_time(t):
            return None
        yi, xi = idx
        ts = t.timestamp()
        sel = np.where(np.abs(self.times_s - ts) <= window_h * 3600 + 1)[0]
        if len(sel) == 0:
            sel = np.array([int(np.argmin(np.abs(self.times_s - ts)))])
        out = []
        for ti in sel:
            w = self.wind_mph[ti, yi, xi]
            p = self.precip_mm_hr[ti, yi, xi]
            k = self.temp_f[ti, yi, xi]
            if np.isnan(w) or np.isnan(p) or np.isnan(k):
                continue
            out.append(CellHour(
                t=datetime.fromtimestamp(int(self.times_s[ti]), tz=timezone.utc),
                wind_mph=float(w), precip_mm_hr=float(p), temp_f=float(k),
            ))
        return out or None

    # --- Browser wire format ----------------------------------------------------------------
    def wire(self, var: str, bbox: Optional[tuple[float, float, float, float]] = None, max_hours: Optional[int] = None) -> dict:
        """Quantized uint8 grid (0 = no data, 1..255 = value) for all hours, base64 encoded.

        The browser receives derived, quantized values only — never the raw forecast table.
        """
        arr = getattr(self, var)
        offset, step = WIRE_STEPS[var]
        ys = np.arange(len(self.lat))
        xs = np.arange(len(self.lon))
        if bbox is not None:
            w, s, e, n = bbox
            ys = np.where((self.lat >= s) & (self.lat <= n))[0]
            xs = np.where((self.lon >= w) & (self.lon <= e))[0]
        ts = np.arange(len(self.times_s)) if max_hours is None else np.arange(min(max_hours, len(self.times_s)))
        sub = arr[np.ix_(ts, ys, xs)]
        q = np.clip(np.round((sub - offset) / step) + 1, 1, 255)
        q = np.where(np.isnan(sub), 0, q).astype(np.uint8)
        return {
            "var": var,
            "offset": offset,
            "step": step,
            "times_s": [int(t) for t in self.times_s[ts]],
            "lat0": float(self.lat[ys[0]]),
            "lon0": float(self.lon[xs[0]]),
            "dlat": self._dlat,
            "dlon": self._dlon,
            "ny": int(len(ys)),
            "nx": int(len(xs)),
            "data_b64": base64.b64encode(q.tobytes()).decode("ascii"),
        }
