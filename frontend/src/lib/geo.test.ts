import { describe, expect, it } from "vitest";
import { BASELINE, CORE, DETOUR, SAMPLES, SHOULDER } from "../fixtures/synthetic";
import { boundsOf, farthestFrom, floorIndex, haversineKm, midpoint, partialPath, positionAt, statusRuns, worstSegment, type LonLat } from "./geo";

describe("positionAt", () => {
  const end = BASELINE.path_t_s.at(-1)!;

  it("waits at the origin before departure and parks at the destination after arrival", () => {
    expect(positionAt(BASELINE, -60)).toMatchObject({ lonlat: [-110, 40], progress: 0, state: "waiting" });
    expect(positionAt(BASELINE, end + 60)).toMatchObject({ lonlat: [-105, 40], progress: 1, state: "arrived" });
  });

  it("interpolates between vertices by the route's own ETAs, heading east", () => {
    const p = positionAt(BASELINE, end / 2);
    expect(p.state).toBe("driving");
    expect(p.progress).toBeCloseTo(0.5, 6);
    expect(p.lonlat[0]).toBeCloseTo(-107.5, 6);
    expect(p.lonlat[1]).toBeCloseTo(40, 6);
    expect(p.bearing).toBeGreaterThan(88);
    expect(p.bearing).toBeLessThan(92);
  });
});

describe("partialPath / midpoint", () => {
  it("returns the first fraction of a path by distance, ending on an interpolated point", () => {
    const quarter = partialPath(BASELINE.path, 0.25);
    expect(quarter.at(-1)![0]).toBeCloseTo(-108.75, 2);
    expect(partialPath(BASELINE.path, 0)).toEqual([BASELINE.path[0]]);
    expect(partialPath(BASELINE.path, 1)).toBe(BASELINE.path);
  });

  it("puts the midpoint half-way along by distance, not by vertex count", () => {
    const skewed: LonLat[] = [[0, 0], [0.1, 0], [0.2, 0], [0.3, 0], [4, 0]];
    expect(midpoint(skewed)[0]).toBeCloseTo(2, 2);
  });
});

describe("farthestFrom", () => {
  it("picks the detour vertex farthest from the route it replaces", () => {
    const p = farthestFrom(DETOUR.path, BASELINE.path);
    expect(p[1]).toBeCloseTo(38.6, 6);
  });

  it("skips vertices near the point to avoid, unless nothing else is left", () => {
    const truckAtFarPoint = farthestFrom(DETOUR.path, BASELINE.path);
    const moved = farthestFrom(DETOUR.path, BASELINE.path, truckAtFarPoint);
    expect(haversineKm(moved, truckAtFarPoint)).toBeGreaterThanOrEqual(90);
    expect(farthestFrom(DETOUR.path, BASELINE.path, truckAtFarPoint, 10_000)).toEqual(truckAtFarPoint);
  });

  it("falls back to the midpoint when there is nothing to compare with", () => {
    expect(farthestFrom(BASELINE.path, [])).toEqual(midpoint(BASELINE.path));
  });
});

describe("worstSegment", () => {
  it("names the stretch with the highest peak, whatever its order", () => {
    expect(worstSegment([SHOULDER, CORE])).toBe(CORE);
    expect(worstSegment([CORE, SHOULDER])).toBe(CORE);
    expect(worstSegment([])).toBeUndefined();
  });
});

describe("statusRuns", () => {
  it("splits the lane into contiguous risk runs that share their boundary vertices", () => {
    const runs = statusRuns(BASELINE, SAMPLES);
    expect(runs.map((r) => r.status)).toEqual(["clear", "near", "breach", "near", "clear"]);
    for (let i = 1; i < runs.length; i++) expect(runs[i].path[0]).toEqual(runs[i - 1].path.at(-1));
    expect(runs.reduce((n, r) => n + r.path.length - 1, 0)).toBe(BASELINE.path.length - 1);
  });

  it("is one clear run when there is no forecast along the lane", () => {
    expect(statusRuns(BASELINE, [])).toEqual([{ status: "clear", path: BASELINE.path }]);
  });
});

describe("boundsOf / floorIndex", () => {
  it("covers every path, and is null for nothing", () => {
    expect(boundsOf([BASELINE.path, DETOUR.path])).toEqual([-110, 38.6, -105, 40]);
    expect(boundsOf([])).toBeNull();
  });

  it("finds the last element at or below x, clamped at both ends", () => {
    const xs = [0, 10, 20, 30];
    expect(floorIndex(xs, -5)).toBe(0);
    expect(floorIndex(xs, 10)).toBe(1);
    expect(floorIndex(xs, 29.9)).toBe(2);
    expect(floorIndex(xs, 99)).toBe(3);
  });
});
