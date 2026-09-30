import type { RouteView, SampleStatus, SampleView } from "../types";

export type LonLat = [number, number];
export type Bounds = [west: number, south: number, east: number, north: number];

const R_KM = 6371.0088;
const rad = (d: number) => (d * Math.PI) / 180;

export function haversineKm(a: LonLat, b: LonLat): number {
  const dLat = rad(b[1] - a[1]);
  const dLon = rad(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function cumulativeKm(path: LonLat[]): number[] {
  const out = new Array<number>(path.length);
  let acc = 0;
  for (let i = 0; i < path.length; i++) {
    if (i > 0) acc += haversineKm(path[i - 1], path[i]);
    out[i] = acc;
  }
  return out;
}

/** Index of the last element <= x in an ascending array (0 if x is below the first). */
export function floorIndex(xs: ArrayLike<number>, x: number): number {
  let lo = 0;
  let hi = xs.length - 1;
  if (hi < 0 || x <= xs[0]) return 0;
  if (x >= xs[hi]) return hi;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid;
    else hi = mid;
  }
  return lo;
}

export interface StatusRun {
  status: SampleStatus;
  path: LonLat[];
}

/**
 * Splits a route into contiguous runs by the risk status the assessment gave the nearest sample
 * (samples are ~5 km apart along the same road). Adjacent runs share their boundary vertex so the
 * line has no gaps.
 */
export function statusRuns(route: RouteView, samples: SampleView[]): StatusRun[] {
  const path = route.path;
  if (path.length < 2) return [];
  if (samples.length === 0) return [{ status: "clear", path }];
  const cum = cumulativeKm(path);
  const scale = cum[cum.length - 1] > 0 ? route.distance_km / cum[cum.length - 1] : 1;
  const kms = samples.map((s) => s.km);
  const statusAt = (km: number): SampleStatus => {
    const i = floorIndex(kms, km);
    const j = Math.min(i + 1, samples.length - 1);
    return Math.abs(kms[j] - km) < Math.abs(kms[i] - km) ? samples[j].status : samples[i].status;
  };
  const runs: StatusRun[] = [];
  let current: StatusRun = { status: statusAt(0), path: [path[0]] };
  for (let i = 1; i < path.length; i++) {
    // A segment takes the status at its midpoint, so a short breach between two far-apart
    // vertices still shows.
    const s = statusAt(((cum[i - 1] + cum[i]) / 2) * scale);
    if (s !== current.status) {
      runs.push(current);
      current = { status: s, path: [path[i - 1]] };
    }
    current.path.push(path[i]);
  }
  runs.push(current);
  return runs;
}

export interface Position {
  lonlat: LonLat;
  /** 0 before departure, 1 on arrival. */
  progress: number;
  /** Degrees clockwise from north. */
  bearing: number;
  state: "waiting" | "driving" | "arrived";
}

export function bearingDeg(a: LonLat, b: LonLat): number {
  const y = Math.sin(rad(b[0] - a[0])) * Math.cos(rad(b[1]));
  const x = Math.cos(rad(a[1])) * Math.sin(rad(b[1])) - Math.sin(rad(a[1])) * Math.cos(rad(b[1])) * Math.cos(rad(b[0] - a[0]));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** Where the truck is `t` seconds after its departure, following the route's own ETAs. */
export function positionAt(route: RouteView, t: number): Position {
  const { path, path_t_s: ts } = route;
  const last = path.length - 1;
  if (t <= 0 || last < 1) {
    return { lonlat: path[0], progress: 0, bearing: last >= 1 ? bearingDeg(path[0], path[1]) : 0, state: t <= 0 ? "waiting" : "arrived" };
  }
  if (t >= ts[last]) {
    return { lonlat: path[last], progress: 1, bearing: bearingDeg(path[last - 1], path[last]), state: "arrived" };
  }
  const i = floorIndex(ts, t);
  const span = ts[i + 1] - ts[i];
  const f = span > 0 ? (t - ts[i]) / span : 0;
  const a = path[i];
  const b = path[i + 1];
  return {
    lonlat: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f],
    progress: t / ts[last],
    bearing: bearingDeg(a, b),
    state: "driving",
  };
}

/** The first `frac` of a path by distance (for draw-on animations). */
export function partialPath(path: LonLat[], frac: number, cum = cumulativeKm(path)): LonLat[] {
  if (frac >= 1 || path.length < 2) return path;
  if (frac <= 0) return [path[0]];
  const target = cum[cum.length - 1] * frac;
  const i = floorIndex(cum, target);
  const span = cum[i + 1] - cum[i];
  const f = span > 0 ? (target - cum[i]) / span : 0;
  const a = path[i];
  const b = path[i + 1];
  return [...path.slice(0, i + 1), [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]];
}

export function boundsOf(paths: LonLat[][]): Bounds | null {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const path of paths) {
    for (const [lon, lat] of path) {
      if (lon < w) w = lon;
      if (lon > e) e = lon;
      if (lat < s) s = lat;
      if (lat > n) n = lat;
    }
  }
  return Number.isFinite(w) ? [w, s, e, n] : null;
}

/** Point at the middle of a path by distance: where a route's label chip sits. */
export function midpoint(path: LonLat[]): LonLat {
  const pp = partialPath(path, 0.5);
  return pp[pp.length - 1];
}

/**
 * The vertex of `path` farthest from `other` (flat-earth distance, fine at label scale): where a
 * detour visibly departs from the route it replaces, so a label there never sits on the shared part.
 * Vertices within `avoidKm` of `avoid` (the truck, say) are skipped unless nothing else is left.
 */
export function farthestFrom(path: LonLat[], other: LonLat[], avoid?: LonLat, avoidKm = 90): LonLat {
  if (!other.length) return midpoint(path);
  const step = Math.max(1, Math.floor(other.length / 400));
  const near = (p: LonLat) => avoid !== undefined && Math.hypot((p[0] - avoid[0]) * Math.cos((p[1] * Math.PI) / 180), p[1] - avoid[1]) * 111.2 < avoidKm;
  const pool = path.filter((p) => !near(p));
  let best = path[0];
  let bestD = -1;
  for (const p of pool.length ? pool : path) {
    const k = Math.cos((p[1] * Math.PI) / 180);
    let d = Infinity;
    for (let j = 0; j < other.length; j += step) {
      const dx = (p[0] - other[j][0]) * k;
      const dy = p[1] - other[j][1];
      const dd = dx * dx + dy * dy;
      if (dd < d) d = dd;
    }
    if (d > bestD) {
      bestD = d;
      best = p;
    }
  }
  return best;
}

/** The breach segment with the highest peak: the one the advisory names, so the map agrees with it. */
export function worstSegment<T extends { peak_wind: string }>(segs: T[]): T | undefined {
  let best: T | undefined;
  for (const s of segs) if (!best || parseFloat(s.peak_wind) > parseFloat(best.peak_wind)) best = s;
  return best;
}
