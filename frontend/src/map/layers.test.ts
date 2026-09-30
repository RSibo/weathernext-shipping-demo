import { describe, expect, it } from "vitest";
import { BASELINE, DEPART_S, DETOUR, ENTRY, FORECAST, syntheticRun } from "../fixtures/synthetic";
import { decodeForecast } from "../lib/field";
import { IDLE, runReducer, type RunState } from "../lib/runReducer";
import { buildLayers, truckStates, type SceneInput } from "./layers";

const fold = (n?: number): RunState =>
  syntheticRun()
    .slice(0, n)
    .reduce((s, e) => runReducer(s, { kind: "event", event: e, now: 0 }), IDLE);

const scene = (run: RunState, over: Partial<SceneInput> = {}): SceneInput => ({
  entries: [ENTRY], selectedId: ENTRY.shipment.shipment_id, run, time: DEPART_S, nowTime: DEPART_S, clock: 10_000,
  stage: false, reducedMotion: true, ...over,
});

const ids = (s: SceneInput) => buildLayers(s).map((l) => l.id);
const layer = (s: SceneInput, id: string) => buildLayers(s).find((l) => l.id === id)!;

describe("buildLayers", () => {
  it("draws the fleet scan: the lane in risk colours with its breach glowing, and the truck", () => {
    const got = ids(scene(IDLE));
    expect(got).toEqual(expect.arrayContaining(["fleet-routes", "lane-breach-glow", "lane-casing", "lane-risk", "lane-endpoints", "fleet-trucks", "truck"]));
    expect(got).not.toContain("recommended");
    expect(got.some((id) => id.startsWith("candidate"))).toBe(false);
  });

  it("flashes the candidates the detour search returned, before the decision", () => {
    const mid = fold(12); // through detour_computed
    const got = ids(scene(mid));
    expect(got).toEqual(expect.arrayContaining(["candidates-hazardous", "candidates", "candidate-chosen"]));
    expect((layer(scene(mid), "candidate-chosen").props.data as unknown[]).length).toBe(1);
    expect((layer(scene(mid), "candidates-hazardous").props.data as unknown[]).length).toBe(1);
  });

  it("after a REROUTE, dashes the abandoned lane, keeps its breach marked and puts the detour on top", () => {
    const got = ids(scene(fold()));
    expect(got).toEqual(expect.arrayContaining(["lane-abandoned", "lane-avoided-breach", "recommended-casing", "recommended"]));
    expect(got).not.toContain("lane-risk");
    expect(got.indexOf("recommended")).toBeGreaterThan(got.indexOf("lane-abandoned"));
    // The recommended route is drawn once, as "recommended", not again as a candidate.
    expect((layer(scene(fold()), "candidate-chosen").props.data as unknown[]).length).toBe(0);
    expect((layer(scene(fold()), "recommended").props.data as { path: unknown }[])[0].path).toBe(DETOUR.path);
  });

  it("widens lines for the projector in stage mode", () => {
    const w = (stage: boolean) => (layer(scene(IDLE, { stage }), "lane-risk").props.data as { width: number }[])[0].width;
    expect(w(true)).toBeCloseTo(w(false) * 1.5, 6);
  });

  it("adds the forecast field when there is one", () => {
    const forecast = decodeForecast(FORECAST);
    const field = { forecast, image: new ImageData(3, 3), bounds: [-112.5, 37, -103.5, 43] as [number, number, number, number] };
    expect(ids(scene(IDLE, { field }))[0]).toBe("field-wind_mph");
  });
});

describe("truckStates", () => {
  it("drives the baseline before the decision and the detour after a REROUTE", () => {
    const t = DEPART_S + 3600;
    const [before] = truckStates(scene(IDLE, { time: t }));
    const [after] = truckStates(scene(fold(), { time: t }));
    expect(before.pos.lonlat[1]).toBeCloseTo(40, 6);
    expect(after.pos.lonlat[1]).toBeLessThan(40);
    expect(after.pos.state).toBe("driving");
  });

  it("scores the wind at the truck against its own limit, and is unmonitored without a forecast", () => {
    const wind = decodeForecast(FORECAST); // 30 mph everywhere: near a 35 mph limit
    const [truck] = truckStates(scene(IDLE, { wind }));
    expect(truck.wind).toBeCloseTo(30, 6);
    expect(truck.risk).toBe("near");
    expect(truckStates(scene(IDLE))[0]).toMatchObject({ wind: null, risk: "unmonitored" });
  });

  it("keeps a held truck at the origin until its new departure", () => {
    const held = syntheticRun()
      .map((e) => (e.type === "advisory" ? { ...e, status: "HOLD" as const, chosen_option_id: "delay:+6h" } : e))
      .reduce((s, e) => runReducer(s, { kind: "event", event: e, now: 0 }), IDLE);
    const [truck] = truckStates(scene(held, { time: DEPART_S + 5 * 3600 }));
    expect(truck.pos.state).toBe("waiting");
    expect(truck.pos.lonlat).toEqual(BASELINE.path[0]);
  });
});
