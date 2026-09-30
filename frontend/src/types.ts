// Mirrors agent/events.py field for field (EVENT_VERSION 1) plus the REST shapes in api/.
// Change both sides together. Display strings ("+14 min") come from the server's FactSheet;
// raw numbers exist only where the UI computes geometry or scales.

export type Status = "CLEAR" | "MONITOR" | "REROUTE" | "HOLD";
export type Verdict = "clean" | "at_risk" | "hazardous" | "unmonitored";
export type Source = "live" | "replay" | "scenario";
export type Tone = "neutral" | "good" | "warn" | "bad";

export interface PlaceView {
  name: string;
  lat: number;
  lon: number;
}

export interface ShipmentView {
  shipment_id: string;
  lane: string;
  carrier?: string;
  cargo?: string;
  wind_threshold: string;
  wind_threshold_mph: number;
  threshold_basis: string;
  depart: string;
  deadline?: string;
  timezone: string;
  depart_iso?: string;
  deadline_iso?: string;
  origin: PlaceView;
  destination: PlaceView;
  stops: PlaceView[];
}

export interface Provenance {
  source: Source;
  init_time: string;
  label: string;
  dataset?: string;
}

export interface SegmentView {
  kind: "breach" | "near";
  start_km: number;
  end_km: number;
  start_t_s: number;
  end_t_s: number;
  stretch: string;
  place: string;
  window: string;
  length: string;
  peak_wind: string;
  hazards: string[];
  peak_lat: number;
  peak_lon: number;
}

export interface RouteView {
  id: string;
  description: string;
  source: "default" | "alternative" | "via_anchor";
  via?: string;
  distance: string;
  duration: string;
  arrive: string;
  distance_km: number;
  path: [number, number][];
  path_t_s: number[];
}

export type SampleStatus = "clear" | "near" | "breach" | "unmonitored";

export interface SampleView {
  km: number;
  lon: number;
  lat: number;
  t_s: number;
  wind?: number;
  precip?: number;
  status: SampleStatus;
}

export interface AssessmentView {
  route_id: string;
  verdict: Verdict;
  threshold_mph: number;
  peak_wind?: string;
  exposure?: string;
  unmonitored_fraction: number;
  segments: SegmentView[];
  near_segments: SegmentView[];
}

export interface CandidateView {
  option_id: string;
  label: string;
  route: RouteView;
  assessment: AssessmentView;
  delta_distance: string;
  delta_duration: string;
  cost: string;
  meets_deadline?: boolean;
}

export interface DelayOptionView {
  option_id: string;
  shift: string;
  shift_hours: number;
  depart: string;
  arrive: string;
  verdict: "clean" | "at_risk" | "hazardous" | "beyond_forecast";
  peak_wind?: string;
  meets_deadline?: boolean;
}

export interface Metric {
  key: string;
  label: string;
  value: string;
  tone: Tone;
}

export interface TokenUsage {
  prompt: number;
  output: number;
  thoughts: number;
}

interface EventBase {
  v: number;
  run_id: string;
  seq: number;
  t_ms: number;
}

export interface RunStarted extends EventBase {
  type: "run_started";
  shipment: ShipmentView;
  mode: Source;
  provenance: Provenance;
  model: string;
  recorded: boolean;
  note?: string;
}

export interface RoutePlanned extends EventBase {
  type: "route_planned";
  route: RouteView;
}

export interface ModelTurn extends EventBase {
  type: "model_turn";
  turn: number;
  phase: "start" | "end";
  model: string;
  latency_ms?: number;
  tokens?: TokenUsage;
  thought?: string;
}

export interface ToolCall extends EventBase {
  type: "tool_call";
  call_id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResult extends EventBase {
  type: "tool_result";
  call_id: string;
  name: string;
  ok: boolean;
  latency_ms: number;
  summary: string;
  error?: string;
}

export interface Guardrail extends EventBase {
  type: "guardrail";
  kind: "tool_blocked" | "advisory_rejected" | "advisory_accepted" | "fallback_used";
  message: string;
  issues: string[];
}

export interface WeatherChecked extends EventBase {
  type: "weather_checked";
  assessment: AssessmentView;
  method: string;
  sample_spacing_km: number;
  samples: SampleView[];
  hazard_zones?: GeoJSON.Geometry | null;
}

export interface DetourComputed extends EventBase {
  type: "detour_computed";
  candidates: CandidateView[];
  chosen_id?: string;
  still_hazardous: boolean;
  anchors_tried: string[];
}

export interface DelayEvaluated extends EventBase {
  type: "delay_evaluated";
  options: DelayOptionView[];
  earliest_safe_id?: string;
}

export interface Advisory extends EventBase {
  type: "advisory";
  status: Status;
  chosen_option_id: string;
  headline: string;
  recommendation: string;
  rationale: string;
  driver_message: string;
  metrics: Metric[];
  source: "gemini" | "fallback";
  grounded_numbers: number;
}

export interface RunCompleted extends EventBase {
  type: "run_completed";
  status: Status;
  turns: number;
  total_ms: number;
  llm_ms: number;
  tools_ms: number;
  tokens: TokenUsage;
  routes_live: number;
  routes_cached: number;
  model: string;
}

export interface RunError extends EventBase {
  type: "error";
  code: string;
  message: string;
  retryable: boolean;
  fallback?: "golden";
}

export type RunEvent =
  | RunStarted
  | RoutePlanned
  | ModelTurn
  | ToolCall
  | ToolResult
  | Guardrail
  | WeatherChecked
  | DetourComputed
  | DelayEvaluated
  | Advisory
  | RunCompleted
  | RunError;

export const EVENT_TYPES: ReadonlySet<RunEvent["type"]> = new Set([
  "run_started", "route_planned", "model_turn", "tool_call", "tool_result", "guardrail",
  "weather_checked", "detour_computed", "delay_evaluated", "advisory", "run_completed", "error",
]);

// --- REST (api/main.py, api/fleet.py) -----------------------------------------------------------
export interface ModeInfo {
  id: Source;
  label: string;
  init_time: string;
}

export interface AppConfig {
  maps_api_key: string;
  map_id: string;
  model: string;
  event_version: number;
  default_mode: Source;
  modes: ModeInfo[];
  golden: string[];
  hero_shipment: string;
  live_runs_left_today: number;
}

export type Hint = "CLEAR" | "MONITOR" | "ACTION" | "UNKNOWN";

export interface FleetEntry {
  shipment: ShipmentView;
  hint: Hint;
  route?: RouteView;
  assessment?: AssessmentView;
  samples: SampleView[];
  first_breach?: string;
  error?: string;
}

export interface Fleet {
  mode: Source;
  provenance: Provenance;
  now: string;
  now_iso: string;
  summary: string;
  scan_ms: number;
  entries: FleetEntry[];
}

export type FieldVar = "wind_mph" | "precip_mm_hr";

export interface ForecastWire {
  var: FieldVar;
  offset: number;
  step: number;
  times_s: number[]; // valid time, epoch seconds
  lat0: number; // south row centre
  lon0: number; // west column centre
  dlat: number;
  dlon: number;
  ny: number;
  nx: number;
  data_b64: string; // uint8 [t][y][x], 0 = no data, value = offset + (q - 1) * step
  init_time: string;
  now_s: number;
}
