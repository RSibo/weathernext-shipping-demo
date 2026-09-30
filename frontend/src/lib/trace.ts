import type {
  Advisory,
  DelayEvaluated,
  DetourComputed,
  Guardrail,
  ModelTurn,
  RoutePlanned,
  RunCompleted,
  RunError,
  RunEvent,
  RunStarted,
  ToolCall,
  ToolResult,
  WeatherChecked,
} from "../types";

/** One tool invocation with everything it produced. */
export interface ToolStep {
  call: ToolCall;
  result?: ToolResult;
  route?: RoutePlanned;
  weather?: WeatherChecked;
  detour?: DetourComputed;
  delay?: DelayEvaluated;
  guardrails: Guardrail[];
  /** Started together with at least one other call in the same turn. */
  parallel: boolean;
}

export type TraceItem =
  | { kind: "start"; key: string; event: RunStarted }
  | { kind: "turn"; key: string; turn: number; start: ModelTurn; end?: ModelTurn; tools: ToolStep[] }
  | { kind: "system"; key: string; tools: ToolStep[] }
  | { kind: "guardrail"; key: string; event: Guardrail }
  | { kind: "advisory"; key: string; event: Advisory }
  | { kind: "done"; key: string; event: RunCompleted }
  | { kind: "error"; key: string; event: RunError };

const DOMAIN_TOOL: Record<string, "check_route_weather" | "compute_detour_route" | "evaluate_departure_delay"> = {
  route_planned: "check_route_weather",
  weather_checked: "check_route_weather",
  detour_computed: "compute_detour_route",
  delay_evaluated: "evaluate_departure_delay",
};

/**
 * Groups a run's flat event list into what a person reads: Gemini turns, each with the tool
 * calls it decided on, each tool with its result and the evidence it produced.
 */
export function buildTrace(events: RunEvent[]): TraceItem[] {
  const items: TraceItem[] = [];
  const steps = new Map<string, ToolStep>();
  let group: Extract<TraceItem, { kind: "turn" | "system" }> | undefined;

  const openStep = (name: string): ToolStep | undefined => {
    const all = [...steps.values()].reverse();
    return all.find((s) => s.call.name === name && !s.result) ?? all.find((s) => s.call.name === name);
  };

  for (const e of events) {
    switch (e.type) {
      case "run_started":
        items.push({ kind: "start", key: `start-${e.seq}`, event: e });
        break;
      case "model_turn":
        if (e.phase === "start") {
          group = { kind: "turn", key: `turn-${e.turn}-${e.seq}`, turn: e.turn, start: e, tools: [] };
          items.push(group);
        } else if (group?.kind === "turn" && group.turn === e.turn) {
          group.end = e;
        }
        break;
      case "tool_call": {
        const turnDone = group?.kind === "turn" && group.end;
        const isSystem = e.call_id.startsWith("sys-");
        if (!group || (isSystem && group.kind !== "system") || (!isSystem && group.kind === "turn" && !turnDone)) {
          group = { kind: "system", key: `system-${e.seq}`, tools: [] };
          items.push(group);
        }
        const step: ToolStep = { call: e, guardrails: [], parallel: false };
        group.tools.push(step);
        steps.set(e.call_id, step);
        break;
      }
      case "tool_result": {
        const step = steps.get(e.call_id);
        if (step) step.result = e;
        break;
      }
      case "route_planned":
      case "weather_checked":
      case "detour_computed":
      case "delay_evaluated": {
        const step = openStep(DOMAIN_TOOL[e.type]);
        if (!step) break;
        if (e.type === "route_planned") step.route = e;
        else if (e.type === "weather_checked") step.weather = e;
        else if (e.type === "detour_computed") step.detour = e;
        else step.delay = e;
        break;
      }
      case "guardrail": {
        const step = [...steps.values()].reverse().find((s) => !s.result);
        if (step && e.kind !== "fallback_used") step.guardrails.push(e);
        else items.push({ kind: "guardrail", key: `guard-${e.seq}`, event: e });
        break;
      }
      case "advisory":
        items.push({ kind: "advisory", key: `adv-${e.seq}`, event: e });
        break;
      case "run_completed":
        items.push({ kind: "done", key: `done-${e.seq}`, event: e });
        break;
      case "error":
        items.push({ kind: "error", key: `err-${e.seq}`, event: e });
        break;
    }
  }
  for (const it of items) {
    if ((it.kind === "turn" || it.kind === "system") && it.tools.length > 1) {
      const middle = it.tools.filter((t) => t.call.name !== "submit_advisory");
      if (middle.length > 1) middle.forEach((t) => (t.parallel = true));
    }
  }
  return items;
}

/** Plain-language title for each tool, so the trace reads as steps, not function names. */
export const TOOL_TITLE: Record<string, string> = {
  check_route_weather: "Check WeatherNext 3 along the route",
  compute_detour_route: "Search for a safer route",
  evaluate_departure_delay: "Test later departures",
  submit_advisory: "Submit advisory for validation",
};

export function toolTitle(name: string): string {
  return TOOL_TITLE[name] ?? name;
}
