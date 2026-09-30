import type {
  Advisory,
  DelayEvaluated,
  DetourComputed,
  RouteView,
  RunCompleted,
  RunEvent,
  RunStarted,
  WeatherChecked,
} from "../types";

export type RunPhase = "idle" | "connecting" | "running" | "done" | "error" | "cancelled";
export type Transport = "live" | "golden" | "list";

export interface RunFailure {
  code: string;
  message: string;
  /** A recorded run exists for this shipment, so the UI can offer it. */
  fallback?: "golden";
}

/** Everything the UI knows about one agent run, folded from its event stream. */
export interface RunState {
  phase: RunPhase;
  transport: Transport | null;
  runId?: string;
  started?: RunStarted;
  route?: RouteView;
  weather?: WeatherChecked;
  detour?: DetourComputed;
  delay?: DelayEvaluated;
  advisory?: Advisory;
  completed?: RunCompleted;
  failure?: RunFailure;
  events: RunEvent[];
  /** Client clock (performance.now) when each milestone arrived; drives map animations. */
  at: Partial<Record<"started" | "weather" | "detour" | "advisory" | "completed", number>>;
}

export type RunAction =
  | { kind: "connect"; transport: Transport; now: number }
  | { kind: "event"; event: RunEvent; now: number }
  | { kind: "fail"; failure: RunFailure }
  | { kind: "cancel" }
  | { kind: "reset" };

export const IDLE: RunState = { phase: "idle", transport: null, events: [], at: {} };

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.kind) {
    case "reset":
      return IDLE;
    case "connect":
      return { ...IDLE, phase: "connecting", transport: action.transport, at: { started: action.now } };
    case "cancel":
      return state.phase === "connecting" || state.phase === "running" ? { ...state, phase: "cancelled" } : state;
    case "fail":
      return { ...state, phase: "error", failure: action.failure };
    case "event":
      return applyEvent(state, action.event, action.now);
  }
}

function applyEvent(state: RunState, e: RunEvent, now: number): RunState {
  if (e.type === "run_started") {
    // A new run id starts from scratch; the same id again is a duplicate and is ignored.
    if (state.runId === e.run_id) return state;
    return {
      ...IDLE, phase: "running", transport: state.transport, runId: e.run_id, started: e, events: [e],
      at: { started: state.at.started ?? now },
    };
  }
  if (state.runId !== undefined && e.run_id !== state.runId) return state; // stray frame from an old run
  const last = state.events[state.events.length - 1];
  if (last && e.seq <= last.seq) return state; // duplicate or out of order
  const next: RunState = { ...state, events: [...state.events, e], phase: state.phase === "connecting" ? "running" : state.phase };
  switch (e.type) {
    case "route_planned":
      return { ...next, route: e.route };
    case "weather_checked":
      return { ...next, weather: e, at: { ...next.at, weather: now } };
    case "detour_computed":
      return { ...next, detour: e, at: { ...next.at, detour: now } };
    case "delay_evaluated":
      return { ...next, delay: e };
    case "advisory":
      return { ...next, advisory: e, at: { ...next.at, advisory: now } };
    case "run_completed":
      return { ...next, completed: e, phase: "done", at: { ...next.at, completed: now } };
    case "error":
      return { ...next, phase: "error", failure: { code: e.code, message: e.message, fallback: e.fallback } };
    default:
      return next;
  }
}

export function isActive(run: RunState | undefined): boolean {
  return run?.phase === "connecting" || run?.phase === "running";
}

/** The route the truck should follow once the agent has decided (REROUTE → the chosen detour). */
export function recommendedRoute(run: RunState): RouteView | undefined {
  const a = run.advisory;
  if (a?.status === "REROUTE") {
    return run.detour?.candidates.find((c) => c.option_id === a.chosen_option_id)?.route;
  }
  return undefined;
}

/** Hours the departure moves when the agent holds the truck for a specific delay option. */
export function holdShiftHours(run: RunState): number {
  const a = run.advisory;
  if (a?.status !== "HOLD") return 0;
  return run.delay?.options.find((o) => o.option_id === a.chosen_option_id)?.shift_hours ?? 0;
}
