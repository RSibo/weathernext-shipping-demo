import type { FieldVar, ForecastWire } from "../types";
import { floorIndex } from "./geo";

/** A decoded forecast variable: every hour of a quantized grid, ready to scrub locally. */
export interface Forecast {
  var: FieldVar;
  times: number[]; // valid time, epoch seconds, ascending
  lat0: number;
  lon0: number;
  dlat: number;
  dlon: number;
  ny: number;
  nx: number;
  offset: number;
  step: number;
  q: Uint8Array; // [t][y][x], row 0 = south; 0 = no data
  initTime: number; // epoch seconds
  nowTime: number; // story "now", epoch seconds
}

export function decodeForecast(w: ForecastWire): Forecast {
  const bin = atob(w.data_b64);
  const q = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) q[i] = bin.charCodeAt(i);
  const initTime = Date.parse(w.init_time) / 1000;
  return {
    var: w.var, times: w.times_s, lat0: w.lat0, lon0: w.lon0, dlat: w.dlat, dlon: w.dlon, ny: w.ny, nx: w.nx,
    offset: w.offset, step: w.step, q, initTime, nowTime: initTime + w.now_s,
  };
}

/** Grid extent as [west, south, east, north], cell edges (not centres). */
export function forecastBounds(f: Forecast): [number, number, number, number] {
  return [
    f.lon0 - f.dlon / 2, f.lat0 - f.dlat / 2,
    f.lon0 + (f.nx - 0.5) * f.dlon, f.lat0 + (f.ny - 0.5) * f.dlat,
  ];
}

export interface Frame {
  i0: number;
  i1: number;
  frac: number;
}

export function frameAt(f: Forecast, t: number): Frame {
  const i0 = floorIndex(f.times, t);
  const i1 = Math.min(i0 + 1, f.times.length - 1);
  const span = f.times[i1] - f.times[i0];
  const frac = span > 0 ? Math.min(1, Math.max(0, (t - f.times[i0]) / span)) : 0;
  return { i0, i1, frac };
}

/**
 * The grid at time t (linear between forecast hours) as an RGBA image, north row first:
 * R = quantized step index (value = offset + R * step), A = 255 where there is data.
 * The GPU filters it bilinearly and FieldLayer classes it into bands, so edges stay smooth at
 * any zoom and the CPU only touches ny × nx values per frame.
 */
export function fieldImage(f: Forecast, t: number): ImageData {
  const { i0, i1, frac } = frameAt(f, t);
  const n = f.ny * f.nx;
  const a = f.q.subarray(i0 * n, (i0 + 1) * n);
  const b = f.q.subarray(i1 * n, (i1 + 1) * n);
  const px = new Uint8ClampedArray(n * 4);
  for (let y = 0; y < f.ny; y++) {
    const src = y * f.nx;
    const dst = (f.ny - 1 - y) * f.nx; // flip: image row 0 is north
    for (let x = 0; x < f.nx; x++) {
      const qa = a[src + x];
      const qb = b[src + x];
      const o = (dst + x) * 4;
      if (qa === 0 && qb === 0) continue;
      const va = qa === 0 ? qb : qa;
      const vb = qb === 0 ? qa : qb;
      px[o] = Math.round(va + (vb - va) * frac) - 1;
      px[o + 3] = 255;
    }
  }
  return new ImageData(px, f.nx, f.ny);
}

/** Forecast value at a point and time (bilinear in space, linear in time), or null outside. */
export function sampleAt(f: Forecast, lon: number, lat: number, t: number): number | null {
  const gx = (lon - f.lon0) / f.dlon;
  const gy = (lat - f.lat0) / f.dlat;
  if (gx < 0 || gy < 0 || gx > f.nx - 1 || gy > f.ny - 1) return null;
  const { i0, i1, frac } = frameAt(f, t);
  const x0 = Math.floor(gx);
  const y0 = Math.floor(gy);
  const x1 = Math.min(x0 + 1, f.nx - 1);
  const y1 = Math.min(y0 + 1, f.ny - 1);
  const fx = gx - x0;
  const fy = gy - y0;
  const n = f.ny * f.nx;
  let acc = 0;
  let wsum = 0;
  for (const [ti, wt] of [[i0, 1 - frac], [i1, frac]] as const) {
    if (wt === 0) continue;
    for (const [yy, xx, w] of [[y0, x0, (1 - fx) * (1 - fy)], [y0, x1, fx * (1 - fy)], [y1, x0, (1 - fx) * fy], [y1, x1, fx * fy]] as const) {
      const q = f.q[ti * n + yy * f.nx + xx];
      if (q === 0 || w === 0) continue;
      acc += (f.offset + (q - 1) * f.step) * w * wt;
      wsum += w * wt;
    }
  }
  return wsum > 0 ? acc / wsum : null;
}

/** Display classes for each variable: thresholds with the band colour starting at each. */
export interface FieldClasses {
  unit: string;
  label: string;
  bins: number[];
  /** Values drawn as isolines (decision thresholds). */
  isolines: number[];
}

export const FIELD_CLASSES: Record<FieldVar, FieldClasses> = {
  wind_mph: { unit: "mph", label: "p90 sustained wind, 10 m", bins: [25, 35, 45, 55], isolines: [35, 45] },
  precip_mm_hr: { unit: "mm/hr", label: "p90 precipitation", bins: [2.5, 5, 10, 25], isolines: [25] },
};
