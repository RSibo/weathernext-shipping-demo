import { describe, expect, it } from "vitest";
import { syntheticRun } from "../fixtures/synthetic";
import type { RunEvent } from "../types";
import { buildTrace, toolTitle, type TraceItem } from "./trace";

type Group = Extract<TraceItem, { kind: "turn" | "system" }>;

describe("buildTrace", () => {
  const items = buildTrace(syntheticRun());

  it("reads as start, three Gemini turns, the advisory, and done", () => {
    expect(items.map((i) => i.kind)).toEqual(["start", "turn", "turn", "turn", "advisory", "done"]);
  });

  it("puts each tool under the turn that decided on it, with its result and evidence", () => {
    const [t1, t2, t3] = items.filter((i): i is Group => i.kind === "turn");
    expect(t1.tools.map((s) => s.call.name)).toEqual(["check_route_weather"]);
    const weather = t1.tools[0];
    expect(weather.result?.ok).toBe(true);
    expect(weather.route?.route.id).toBe("base");
    expect(weather.weather?.assessment.verdict).toBe("hazardous");

    expect(t2.tools.map((s) => s.call.name)).toEqual(["compute_detour_route", "evaluate_departure_delay"]);
    expect(t2.tools[0].detour?.chosen_id).toBe("detour:south");
    expect(t2.tools[1].delay?.earliest_safe_id).toBe("delay:+6h");

    expect(t3.tools[0].call.name).toBe("submit_advisory");
    expect(t3.tools[0].guardrails.map((g) => g.kind)).toEqual(["advisory_accepted"]);
  });

  it("marks tools started together as parallel, but never the submission", () => {
    const turns = items.filter((i): i is Group => i.kind === "turn");
    expect(turns[0].tools[0].parallel).toBe(false);
    expect(turns[1].tools.every((s) => s.parallel)).toBe(true);
    expect(turns[2].tools[0].parallel).toBe(false);
  });

  it("groups server-side calls (sys- ids) outside any Gemini turn", () => {
    const run = syntheticRun();
    const sys: RunEvent[] = [
      { v: 1, run_id: run[0].run_id, seq: 2, t_ms: 10, type: "tool_call", call_id: "sys-1", name: "check_route_weather", args: {} },
      { v: 1, run_id: run[0].run_id, seq: 3, t_ms: 20, type: "tool_result", call_id: "sys-1", name: "check_route_weather", ok: true, latency_ms: 10, summary: "ok" },
    ];
    const trace = buildTrace([run[0], ...sys]);
    expect(trace.map((i) => i.kind)).toEqual(["start", "system"]);
    expect((trace[1] as Group).tools[0].result?.summary).toBe("ok");
  });

  it("shows a fallback as its own row rather than hiding it under a tool", () => {
    const run = syntheticRun();
    const fallback: RunEvent = { v: 1, run_id: run[0].run_id, seq: 99, t_ms: 9000, type: "guardrail", kind: "fallback_used", message: "Template advisory", issues: [] };
    const trace = buildTrace([...run.slice(0, 19), fallback]);
    expect(trace.at(-1)?.kind).toBe("guardrail");
  });

  it("titles tools in plain language and passes unknown names through", () => {
    expect(toolTitle("compute_detour_route")).toBe("Search for a safer route");
    expect(toolTitle("mystery_tool")).toBe("mystery_tool");
  });
});
