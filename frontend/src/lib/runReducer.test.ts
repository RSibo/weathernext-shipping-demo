import { describe, expect, it } from "vitest";
import { DETOUR, RUN_ID, syntheticRun } from "../fixtures/synthetic";
import type { RunEvent } from "../types";
import { holdShiftHours, IDLE, isActive, recommendedRoute, runReducer, type RunState } from "./runReducer";

const fold = (events: RunEvent[], from: RunState = IDLE) => events.reduce((s, e, i) => runReducer(s, { kind: "event", event: e, now: 1000 + i }), from);

describe("runReducer", () => {
  it("folds a full REROUTE run into its final state", () => {
    const events = syntheticRun();
    const s = fold(events, runReducer(IDLE, { kind: "connect", transport: "golden", now: 500 }));
    expect(s.phase).toBe("done");
    expect(s.transport).toBe("golden");
    expect(s.runId).toBe(RUN_ID);
    expect(s.events).toHaveLength(events.length);
    expect(s.route?.id).toBe("base");
    expect(s.weather?.assessment.segments).toHaveLength(2);
    expect(s.detour?.chosen_id).toBe("detour:south");
    expect(s.delay?.earliest_safe_id).toBe("delay:+6h");
    expect(s.advisory?.status).toBe("REROUTE");
    expect(s.completed?.turns).toBe(3);
    // The connect time survives run_started, so the run clock counts from the click.
    expect(s.at.started).toBe(500);
    expect(s.at.weather).toBeGreaterThan(s.at.started!);
    expect(s.at.completed).toBeGreaterThanOrEqual(s.at.advisory!);
    expect(isActive(s)).toBe(false);
  });

  it("is running once connected and until the run completes", () => {
    const connecting = runReducer(IDLE, { kind: "connect", transport: "live", now: 0 });
    expect(connecting.phase).toBe("connecting");
    expect(isActive(connecting)).toBe(true);
    const mid = fold(syntheticRun().slice(0, 6), connecting);
    expect(mid.phase).toBe("running");
    expect(isActive(mid)).toBe(true);
    expect(mid.weather).toBeDefined();
    expect(mid.advisory).toBeUndefined();
  });

  it("ignores duplicate and out-of-order frames", () => {
    const events = syntheticRun();
    const once = fold(events.slice(0, 8));
    const replayed = fold([events[7], events[3], events[0]], once);
    expect(replayed.events).toHaveLength(8);
    expect(replayed).toBe(once);
  });

  it("ignores frames from another run, and a new run id starts over", () => {
    const first = fold(syntheticRun().slice(0, 10));
    const stray = syntheticRun("run-old")[11];
    expect(runReducer(first, { kind: "event", event: stray, now: 0 })).toBe(first);
    const second = fold(syntheticRun("run-2").slice(0, 1), first);
    expect(second.runId).toBe("run-2");
    expect(second.events).toHaveLength(1);
    expect(second.weather).toBeUndefined();
  });

  it("turns an error event into a failure the panel can offer a fallback for", () => {
    const events = syntheticRun();
    const err: RunEvent = { v: 1, run_id: RUN_ID, seq: 7, t_ms: 1400, type: "error", code: "vertex_unavailable", message: "Gemini is unavailable", retryable: true, fallback: "golden" };
    const s = fold([...events.slice(0, 6), err]);
    expect(s.phase).toBe("error");
    expect(s.failure).toEqual({ code: "vertex_unavailable", message: "Gemini is unavailable", fallback: "golden" });
  });

  it("cancels only an active run", () => {
    const done = fold(syntheticRun());
    expect(runReducer(done, { kind: "cancel" })).toBe(done);
    const mid = fold(syntheticRun().slice(0, 5));
    expect(runReducer(mid, { kind: "cancel" }).phase).toBe("cancelled");
    expect(runReducer(mid, { kind: "reset" })).toBe(IDLE);
  });
});

describe("recommendedRoute / holdShiftHours", () => {
  it("follows the chosen detour on REROUTE and does not hold", () => {
    const s = fold(syntheticRun());
    expect(recommendedRoute(s)).toBe(DETOUR);
    expect(holdShiftHours(s)).toBe(0);
  });

  it("moves the departure by the chosen delay on HOLD", () => {
    const events = syntheticRun().map((e) => (e.type === "advisory" ? { ...e, status: "HOLD" as const, chosen_option_id: "delay:+6h" } : e));
    const s = fold(events);
    expect(recommendedRoute(s)).toBeUndefined();
    expect(holdShiftHours(s)).toBe(6);
  });
});
