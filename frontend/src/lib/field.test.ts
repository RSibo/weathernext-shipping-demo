import { describe, expect, it } from "vitest";
import { DEPART_S, FORECAST } from "../fixtures/synthetic";
import type { ForecastWire } from "../types";
import { decodeForecast, fieldImage, forecastBounds, frameAt, sampleAt } from "./field";

/** A 2 × 2 grid for two hours: hour 0 rises west → east (20, 40 mph), hour 1 is 40 everywhere; one cell has no data. */
function tiny(): ForecastWire {
  const q = [5, 9, 5, 9, 9, 9, 9, 0]; // value = offset + (q - 1) * step; 0 = no data
  return { ...FORECAST, ny: 2, nx: 2, lat0: 40, lon0: -110, dlat: 1, dlon: 1, data_b64: btoa(String.fromCharCode(...q)) };
}

describe("decodeForecast", () => {
  it("unpacks the grid and anchors story time on the forecast issue time", () => {
    const f = decodeForecast(FORECAST);
    expect(f.q).toHaveLength(18);
    expect(f.initTime).toBe(Date.parse("2026-03-12T06:00:00Z") / 1000);
    expect(f.nowTime).toBe(DEPART_S);
    expect(forecastBounds(f)).toEqual([-112.5, 37, -103.5, 43]);
  });
});

describe("frameAt", () => {
  it("clamps outside the forecast and interpolates inside it", () => {
    const f = decodeForecast(FORECAST);
    expect(frameAt(f, DEPART_S - 999)).toEqual({ i0: 0, i1: 1, frac: 0 });
    expect(frameAt(f, DEPART_S + 900)).toEqual({ i0: 0, i1: 1, frac: 0.25 });
    expect(frameAt(f, DEPART_S + 9999)).toEqual({ i0: 1, i1: 1, frac: 0 });
  });
});

describe("sampleAt", () => {
  const f = decodeForecast(tiny());

  it("is bilinear in space and linear in time", () => {
    expect(sampleAt(f, -110, 40, DEPART_S)).toBe(20);
    expect(sampleAt(f, -109.5, 40, DEPART_S)).toBeCloseTo(30, 6);
    expect(sampleAt(f, -110, 40, DEPART_S + 1800)).toBeCloseTo(30, 6);
  });

  it("averages only the cells that have data, and is null off the grid", () => {
    expect(sampleAt(f, -109.25, 40.75, DEPART_S + 3600)).toBeCloseTo(40, 6); // the nearest corner is missing: neighbours carry it
    expect(sampleAt(f, -109, 41, DEPART_S + 3600)).toBeNull(); // exactly on it: nothing to lean on
    expect(sampleAt(f, -111, 40, DEPART_S)).toBeNull();
    expect(sampleAt(f, -110, 42, DEPART_S)).toBeNull();
  });
});

describe("fieldImage", () => {
  it("writes the step index north row first, transparent where there is no data", () => {
    const f = decodeForecast(tiny());
    const img = fieldImage(f, DEPART_S + 3600);
    const px = (x: number, y: number) => [...img.data.slice((y * 2 + x) * 4, (y * 2 + x) * 4 + 4)];
    expect(px(0, 0)).toEqual([8, 0, 0, 255]); // north-west
    expect(px(1, 0)).toEqual([0, 0, 0, 0]); // north-east: no data
    expect(px(0, 1)).toEqual([8, 0, 0, 255]); // south-west
  });
});
