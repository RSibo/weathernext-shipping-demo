// A hand-made REROUTE run on an invented lane, for tests. Geometry and numbers are synthetic on
// purpose: recorded runs carry Google Maps route geometry and WeatherNext values, which stay out of git.
//
// Story: Alpha → Bravo runs due east along 40°N; wind crosses the 35 mph limit mid-lane. Gemini
// checks the forecast, then searches detours and later departures in parallel, and reroutes via a
// southern loop.

import type {
  AppConfig,
  AssessmentView,
  Fleet,
  FleetEntry,
  ForecastWire,
  RouteView,
  RunEvent,
  SampleView,
  SegmentView,
  ShipmentView,
} from "../types";

export const RUN_ID = "run-synthetic";
export const DEPART_ISO = "2026-03-12T16:00:00Z";
export const DEPART_S = Date.parse(DEPART_ISO) / 1000;

type LonLat = [number, number];
/** Omit that distributes over the event union, so each literal is checked against its own variant. */
type Body<E> = E extends RunEvent ? Omit<E, "v" | "run_id" | "seq" | "t_ms"> : never;

/** A route through `pts`, driven at a steady 90 km/h (≈ 0.8° of longitude per hour at 40°N). */
export function route(id: string, description: string, pts: LonLat[], source: RouteView["source"] = "default"): RouteView {
  const t: number[] = [0];
  for (let i = 1; i < pts.length; i++) {
    const dx = (pts[i][0] - pts[i - 1][0]) * 85.2;
    const dy = (pts[i][1] - pts[i - 1][1]) * 111.2;
    t.push(t[i - 1] + (Math.hypot(dx, dy) / 90) * 3600);
  }
  const km = (t[t.length - 1] / 3600) * 90;
  return {
    id, description, source, distance: `${Math.round(km / 1.609)} mi (${Math.round(km)} km)`, duration: "5 h",
    arrive: "15:00 MDT", distance_km: km, path: pts, path_t_s: t,
  };
}

const line = (a: LonLat, b: LonLat, n: number): LonLat[] =>
  Array.from({ length: n + 1 }, (_, i) => [a[0] + ((b[0] - a[0]) * i) / n, a[1] + ((b[1] - a[1]) * i) / n] as LonLat);

export const BASELINE = route("base", "I-99 E", line([-110, 40], [-105, 40], 20));
export const DETOUR = route(
  "detour:south",
  "I-98 E",
  [...line([-110, 40], [-108.5, 38.6], 6), ...line([-108.5, 38.6], [-106.5, 38.6], 8).slice(1), ...line([-106.5, 38.6], [-105, 40], 6).slice(1)],
  "via_anchor",
);
export const NORTH = route("detour:north", "US-99 N", [...line([-110, 40], [-107.5, 40.6], 6), ...line([-107.5, 40.6], [-105, 40], 6).slice(1)], "alternative");

/** Wind along the baseline: calm, a 36 mph shoulder, a 44 mph core, then calm again. */
function windAt(lon: number): number {
  if (lon > -108.4 && lon < -107.6) return lon > -108.1 && lon < -107.8 ? 44 : 36;
  if (lon > -108.8 && lon < -107.2) return 31;
  return 18;
}

export const SAMPLES: SampleView[] = BASELINE.path.map(([lon, lat], i) => {
  const wind = windAt(lon);
  return { km: i * 21.3, lon, lat, t_s: BASELINE.path_t_s[i], wind, status: wind >= 35 ? "breach" : wind >= 29.75 ? "near" : "clear" };
});

const seg = (kind: SegmentView["kind"], a: number, b: number, place: string, window: string, peak: string, at: LonLat): SegmentView => ({
  kind, start_km: SAMPLES[a].km, end_km: SAMPLES[b].km, start_t_s: SAMPLES[a].t_s, end_t_s: SAMPLES[b].t_s,
  stretch: `km ${Math.round(SAMPLES[a].km)}–${Math.round(SAMPLES[b].km)}`, place, window,
  length: `${Math.round(SAMPLES[b].km - SAMPLES[a].km)} km`, peak_wind: peak, hazards: ["wind"], peak_lat: at[1], peak_lon: at[0],
});

export const SHOULDER = seg("breach", 7, 7, "Charlie, WY", "12:00 MDT", "36 mph", [-108.25, 40]);
export const CORE = seg("breach", 8, 9, "Delta, WY", "12:15–12:45 MDT", "44 mph", [-108, 40]);

export const ASSESSMENT: AssessmentView = {
  route_id: "base", verdict: "hazardous", threshold_mph: 35, peak_wind: "44 mph", exposure: "43 km over the limit",
  unmonitored_fraction: 0, segments: [SHOULDER, CORE], near_segments: [seg("near", 6, 6, "Charlie, WY", "11:45 MDT", "31 mph", [-108.5, 40])],
};

const clean = (id: string): AssessmentView => ({
  route_id: id, verdict: "clean", threshold_mph: 35, peak_wind: "24 mph", unmonitored_fraction: 0, segments: [], near_segments: [],
});

export const SHIPMENT: ShipmentView = {
  shipment_id: "SHP-9001", lane: "Alpha, UT → Bravo, CO", carrier: "Test Freight", cargo: "Empty 53' dry van",
  wind_threshold: "35 mph", wind_threshold_mph: 35, threshold_basis: "Empty high-profile trailer", depart: "Thu 10:00 MDT",
  deadline: "Thu 19:00 MDT", timezone: "America/Denver", depart_iso: DEPART_ISO, deadline_iso: "2026-03-13T01:00:00Z",
  origin: { name: "Alpha, UT", lat: 40, lon: -110 }, destination: { name: "Bravo, CO", lat: 40, lon: -105 }, stops: [],
};

/** The run's events in stream order, with `seq` and `t_ms` filled in. */
export function syntheticRun(runId = RUN_ID): RunEvent[] {
  const base = { v: 1, run_id: runId };
  const events: Body<RunEvent>[] = [
    { type: "run_started", shipment: SHIPMENT, mode: "scenario", provenance: { source: "scenario", init_time: "2026-03-12T06:00:00Z", label: "Synthetic" }, model: "gemini-test", recorded: true },
    { type: "model_turn", turn: 1, phase: "start", model: "gemini-test" },
    { type: "model_turn", turn: 1, phase: "end", model: "gemini-test", latency_ms: 900, tokens: { prompt: 800, output: 20, thoughts: 40 }, thought: "Check the forecast first." },
    { type: "tool_call", call_id: "c1", name: "check_route_weather", args: { shipment_id: "SHP-9001" } },
    { type: "route_planned", route: BASELINE },
    { type: "weather_checked", assessment: ASSESSMENT, method: "p90 at ETA ± 1 h", sample_spacing_km: 5, samples: SAMPLES, hazard_zones: null },
    { type: "tool_result", call_id: "c1", name: "check_route_weather", ok: true, latency_ms: 150, summary: "Over the limit on 2 stretches" },
    { type: "model_turn", turn: 2, phase: "start", model: "gemini-test" },
    { type: "model_turn", turn: 2, phase: "end", model: "gemini-test", latency_ms: 1200, tokens: { prompt: 1600, output: 50, thoughts: 0 } },
    { type: "tool_call", call_id: "c2", name: "compute_detour_route", args: { shipment_id: "SHP-9001" } },
    { type: "tool_call", call_id: "c3", name: "evaluate_departure_delay", args: { shipment_id: "SHP-9001" } },
    {
      type: "detour_computed",
      candidates: [
        { option_id: "detour:south", label: "I-98 E", route: DETOUR, assessment: clean("detour:south"), delta_distance: "+30 mi (+48 km)", delta_duration: "+32 min", cost: "$60 est.", meets_deadline: true },
        { option_id: "detour:north", label: "US-99 N", route: NORTH, assessment: { ...ASSESSMENT, route_id: "detour:north" }, delta_distance: "+9 mi (+14 km)", delta_duration: "+11 min", cost: "$18 est.", meets_deadline: true },
      ],
      chosen_id: "detour:south", still_hazardous: false, anchors_tried: ["Echo, UT"],
    },
    { type: "tool_result", call_id: "c2", name: "compute_detour_route", ok: true, latency_ms: 400, summary: "2 candidates · fastest safe: I-98 E (+32 min)" },
    {
      type: "delay_evaluated",
      options: [
        { option_id: "delay:+2h", shift: "+2 h", shift_hours: 2, depart: "12:00 MDT", arrive: "17:00 MDT", verdict: "hazardous", peak_wind: "40 mph", meets_deadline: true },
        { option_id: "delay:+6h", shift: "+6 h", shift_hours: 6, depart: "16:00 MDT", arrive: "21:00 MDT", verdict: "clean", peak_wind: "22 mph", meets_deadline: false },
      ],
      earliest_safe_id: "delay:+6h",
    },
    { type: "tool_result", call_id: "c3", name: "evaluate_departure_delay", ok: true, latency_ms: 300, summary: "earliest safe: +6 h (misses deadline)" },
    { type: "model_turn", turn: 3, phase: "start", model: "gemini-test" },
    { type: "model_turn", turn: 3, phase: "end", model: "gemini-test", latency_ms: 1500, tokens: { prompt: 2400, output: 300, thoughts: 0 } },
    { type: "tool_call", call_id: "c4", name: "submit_advisory", args: { status: "REROUTE", chosen_option_id: "detour:south" } },
    { type: "guardrail", kind: "advisory_accepted", message: "Decision allowed; 5 of 5 numbers traced", issues: [] },
    { type: "tool_result", call_id: "c4", name: "submit_advisory", ok: true, latency_ms: 5, summary: "accepted" },
    {
      type: "advisory", status: "REROUTE", chosen_option_id: "detour:south", headline: "Reroute via I-98 E: 44 mph winds on I-99 near Delta, WY",
      recommendation: "Take I-98 E. Adds +32 min and +30 mi (+48 km).", rationale: "I-99 E crosses 44 mph winds; I-98 E peaks at 24 mph.",
      driver_message: "Please take I-98 E today.", source: "gemini", grounded_numbers: 5,
      metrics: [
        { key: "delta_duration", label: "Extra time", value: "+32 min", tone: "neutral" },
        { key: "delta_distance", label: "Extra distance", value: "+30 mi (+48 km)", tone: "neutral" },
      ],
    },
    { type: "run_completed", status: "REROUTE", turns: 3, total_ms: 4600, llm_ms: 3600, tools_ms: 855, tokens: { prompt: 4800, output: 370, thoughts: 40 }, routes_live: 0, routes_cached: 3, model: "gemini-test" },
  ];
  return events.map((e, i) => ({ ...base, ...e, seq: i + 1, t_ms: i * 200 }) as RunEvent);
}

export const ENTRY: FleetEntry = { shipment: SHIPMENT, hint: "ACTION", route: BASELINE, assessment: ASSESSMENT, samples: SAMPLES, first_breach: "12:00 MDT near Charlie, WY" };

export const CONFIG: AppConfig = {
  maps_api_key: "", map_id: "", model: "gemini-test", event_version: 1, default_mode: "scenario",
  modes: [{ id: "scenario", label: "Scenario", init_time: "2026-03-12T06:00:00Z" }], golden: ["SHP-9001"], hero_shipment: "SHP-9001", live_runs_left_today: 5,
};

export const FLEET: Fleet = {
  mode: "scenario", provenance: { source: "scenario", init_time: "2026-03-12T06:00:00Z", label: "Synthetic" }, now: "Thu 10:00 MDT",
  now_iso: DEPART_ISO, summary: "1 needs action", scan_ms: 12, entries: [ENTRY],
};

/** A 3 × 3 wind grid over the lane for two hours; every cell reads 30 mph (q = 7 at offset 0, step 5). */
export const FORECAST: ForecastWire = {
  var: "wind_mph", offset: 0, step: 5, times_s: [DEPART_S, DEPART_S + 3600], lat0: 38, lon0: -111, dlat: 2, dlon: 3, ny: 3, nx: 3,
  data_b64: btoa(String.fromCharCode(...new Array(18).fill(7))), init_time: "2026-03-12T06:00:00Z", now_s: DEPART_S - Date.parse("2026-03-12T06:00:00Z") / 1000,
};
