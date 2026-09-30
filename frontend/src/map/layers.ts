// Pure scene → deck.gl layers. Data arrays are memoized on their inputs so that the per-frame
// animation clock only changes uniforms (opacity, radius scale), never re-uploads geometry.

import { COORDINATE_SYSTEM, type Layer } from "@deck.gl/core";
import { PathStyleExtension } from "@deck.gl/extensions";
import { IconLayer, PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { FIELD_CLASSES, sampleAt, type Forecast } from "../lib/field";
import { cumulativeKm, partialPath, positionAt, statusRuns, type LonLat, type Position } from "../lib/geo";
import { CASING, INK, MAP_HINT_COLOR, MUTED, RISK_COLOR, hex, type RGBA } from "../lib/palette";
import { holdShiftHours, recommendedRoute, type RunState } from "../lib/runReducer";
import { epoch } from "../lib/time";
import type { FleetEntry, RouteView, SampleStatus, SampleView } from "../types";
import { FieldLayer, fieldShader } from "./FieldLayer";

export interface SceneInput {
  entries: FleetEntry[];
  selectedId?: string;
  hoverId?: string;
  run: RunState;
  time: number; // epoch seconds on the forecast timeline
  nowTime: number; // story "now": departure fallback for runs recorded without depart_iso
  clock: number; // performance.now(): drives pulses and draw-on
  field?: { forecast: Forecast; image: ImageData; bounds: [number, number, number, number] };
  wind?: Forecast;
  stage: boolean;
  reducedMotion: boolean;
}

export const CANDIDATE_STAGGER_MS = 520;
export const DRAW_MS = 1100;
const dashed = new PathStyleExtension({ dash: true });
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

// --- memo -----------------------------------------------------------------------------------------
const memoStore = new Map<string, { deps: unknown[]; value: unknown }>();
function memo<T>(key: string, deps: unknown[], fn: () => T): T {
  const hit = memoStore.get(key);
  if (hit && hit.deps.length === deps.length && hit.deps.every((d, i) => Object.is(d, deps[i]))) return hit.value as T;
  const value = fn();
  memoStore.set(key, { deps, value });
  return value;
}

// --- shared scene facts (also used by the HTML label layer) ----------------------------------------
/** What the selected lane shows: the agent's evidence once it has some, else the fleet scan. */
export function laneView(entry: FleetEntry | undefined, run: RunState): { route?: RouteView; samples: SampleView[] } {
  return { route: run.route ?? entry?.route, samples: run.weather?.samples ?? entry?.samples ?? [] };
}

/** Departure and the route the truck follows, after the agent's decision if there is one. */
export function truckPlan(entry: FleetEntry, run: RunState | undefined, nowTime: number): { route?: RouteView; depart: number } {
  const depart = epoch(entry.shipment.depart_iso ?? run?.started?.shipment.depart_iso, nowTime);
  if (!run) return { route: entry.route, depart };
  return { route: recommendedRoute(run) ?? run.route ?? entry.route, depart: depart + holdShiftHours(run) * 3600 };
}

export interface TruckState {
  id: string;
  entry: FleetEntry;
  pos: Position;
  /** p90 wind at the truck right now, when the forecast covers it. */
  wind: number | null;
  risk: SampleStatus;
}

export function truckStates(s: Pick<SceneInput, "entries" | "selectedId" | "run" | "time" | "nowTime" | "wind">): TruckState[] {
  const out: TruckState[] = [];
  for (const e of s.entries) {
    const id = e.shipment.shipment_id;
    const plan = truckPlan(e, id === s.selectedId ? s.run : undefined, s.nowTime);
    if (!plan.route || plan.route.path.length < 2) continue;
    const pos = positionAt(plan.route, s.time - plan.depart);
    const wind = s.wind ? sampleAt(s.wind, pos.lonlat[0], pos.lonlat[1], s.time) : null;
    const limit = e.shipment.wind_threshold_mph;
    const risk: SampleStatus = wind === null ? "unmonitored" : wind >= limit ? "breach" : wind >= 0.85 * limit ? "near" : "clear";
    out.push({ id, entry: e, pos, wind, risk });
  }
  return out;
}

// --- layers ---------------------------------------------------------------------------------------
type PathDatum = { path: LonLat[]; color: RGBA; width: number };

function paths(id: string, data: PathDatum[], extra: Record<string, unknown> = {}) {
  return new PathLayer<PathDatum>({
    id,
    data,
    getPath: (d) => d.path,
    getColor: (d) => d.color,
    getWidth: (d) => d.width,
    widthUnits: "pixels",
    capRounded: true,
    jointRounded: true,
    miterLimit: 2,
    parameters: { depthCompare: "always" },
    ...extra,
  });
}

const TRUCK_ICON = (ring: string) =>
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">` +
      `<circle cx="48" cy="48" r="41" fill="#141413" stroke="${ring}" stroke-width="7"/>` +
      `<g transform="translate(25.8 25.8) scale(1.85)" fill="none" stroke="#ffffff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/>` +
      `<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"/>` +
      `<circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/></g></svg>`,
  );

export function buildLayers(s: SceneInput): Layer[] {
  const k = s.stage ? 1.5 : 1;
  const layers: Layer[] = [];
  const selected = s.entries.find((e) => e.shipment.shipment_id === s.selectedId);
  const { route, samples } = laneView(selected, s.run);
  const rerouted = s.run.advisory?.status === "REROUTE" ? recommendedRoute(s.run) : undefined;
  const pulse = s.reducedMotion ? 0.5 : 0.5 + 0.5 * Math.sin(s.clock / 340);

  // 1. Forecast field (GPU-classed; see FieldLayer).
  if (s.field) {
    const { forecast, image, bounds } = s.field;
    const shader = memo("field-shader", [forecast.var, forecast.step, forecast.offset], () =>
      fieldShader(FIELD_CLASSES[forecast.var], forecast.step, forecast.offset),
    );
    layers.push(
      new FieldLayer({
        id: `field-${forecast.var}`,
        image,
        bounds,
        _imageCoordinateSystem: COORDINATE_SYSTEM.LNGLAT,
        filterShader: shader,
        textureParameters: { minFilter: "linear", magFilter: "linear", addressModeU: "clamp-to-edge", addressModeV: "clamp-to-edge" },
        parameters: { depthCompare: "always" },
      }),
    );
  }

  // 2. The rest of the fleet, recessive; the hovered lane lifts.
  layers.push(
    paths(
      "fleet-routes",
      memo("fleet-routes", [s.entries, s.selectedId, s.hoverId, k], () =>
        s.entries
          .filter((e) => e.route && e.shipment.shipment_id !== s.selectedId)
          .map((e) => {
            const hot = e.shipment.shipment_id === s.hoverId;
            return { path: e.route!.path, color: hex(hot ? INK : MUTED, hot ? 210 : 115), width: (hot ? 3 : 2) * k };
          }),
      ),
    ),
  );

  // 3. Detour candidates, drawn on one after another as the agent's search returns them. Before
  //    the decision the tool's pick is highlighted; after a REROUTE the recommended route owns
  //    the top of the stack (layer 4), after anything else no candidate is highlighted.
  const detour = s.run.detour;
  const decided = Boolean(s.run.advisory);
  if (detour && s.run.at.detour !== undefined) {
    const since = s.reducedMotion ? Infinity : s.clock - s.run.at.detour;
    const chosenId = decided ? undefined : detour.chosen_id;
    const cums = memo("cand-cum", [detour], () => detour.candidates.map((c) => cumulativeKm(c.route.path)));
    const progress = detour.candidates.map((_, i) => easeOut(Math.min(1, Math.max(0, (since - i * CANDIDATE_STAGGER_MS) / DRAW_MS))));
    const settled = progress.every((p) => p >= 1);
    const { hazardous, other, chosen } = memo("cand-data", [detour, settled ? -1 : since, chosenId, rerouted, decided, k], () => {
      const vis = detour.candidates
        .map((c, i) => ({ c, path: progress[i] >= 1 ? c.route.path : partialPath(c.route.path, progress[i], cums[i]), p: progress[i] }))
        .filter((d) => d.p > 0 && d.c.route !== rerouted);
      const clean = vis.filter((d) => d.c.assessment.verdict !== "hazardous");
      return {
        hazardous: vis.filter((d) => d.c.assessment.verdict === "hazardous").map((d) => ({ path: d.path, color: hex(RISK_COLOR.breach, 175), width: 2.5 * k })),
        other: clean.filter((d) => d.c.option_id !== chosenId).map((d) => ({ path: d.path, color: hex("#c3c2b7", decided ? 110 : 170), width: 2.5 * k })),
        chosen: clean.filter((d) => d.c.option_id === chosenId).map((d) => ({ path: d.path, color: hex(INK), width: 4 * k })),
      };
    });
    layers.push(
      paths("candidates-hazardous", hazardous, { extensions: [dashed], getDashArray: [3, 2.2], dashJustified: true }),
      paths("candidates", other),
      paths("candidate-chosen-casing", memo("cand-chosen-casing", [chosen], () => chosen.map((d) => ({ ...d, color: hex(CASING), width: d.width + 3 * k })))),
      paths("candidate-chosen", chosen),
    );
  }

  // 4. The selected lane: risk-coloured, or abandoned (dashed) once the agent reroutes.
  if (route) {
    if (rerouted) {
      // The avoided route stays legible, with the stretch that broke the limit still marked: the
      // audience sees what the detour goes around.
      const runs = memo("lane-runs", [route, samples], () => statusRuns(route, samples));
      const avoided = memo("lane-avoided-breach", [runs, k], () => runs.filter((r) => r.status === "breach").map((r) => ({ path: r.path, color: hex(RISK_COLOR.breach, 235), width: 3.5 * k })));
      layers.push(
        paths("lane-abandoned-casing", memo("lane-abandoned-casing", [route, k], () => [{ path: route.path, color: hex(CASING, 190), width: 5.5 * k }])),
        paths("lane-abandoned", memo("lane-abandoned", [route, k], () => [{ path: route.path, color: hex(MUTED, 240), width: 2.5 * k }]), {
          extensions: [dashed], getDashArray: [2, 1.6], dashJustified: true,
        }),
        paths("lane-avoided-breach", avoided),
        paths("recommended-glow", memo("rec-glow", [rerouted, k], () => [{ path: rerouted.path, color: hex(INK, 60), width: 14 * k }]), { opacity: 0.6 + 0.4 * pulse }),
        paths("recommended-casing", memo("rec-casing", [rerouted, k], () => [{ path: rerouted.path, color: hex(CASING), width: 8.5 * k }])),
        paths("recommended", memo("rec", [rerouted, k], () => [{ path: rerouted.path, color: hex("#ffffff"), width: 5 * k }])),
      );
    } else {
      const runs = memo("lane-runs", [route, samples], () => statusRuns(route, samples));
      const breach = memo("lane-breach", [runs, k], () => runs.filter((r) => r.status === "breach").map((r) => ({ path: r.path, color: hex(RISK_COLOR.breach), width: 16 * k })));
      layers.push(
        paths("lane-breach-glow", breach, { opacity: 0.22 + 0.33 * pulse }),
        paths("lane-casing", memo("lane-casing", [route, k], () => [{ path: route.path, color: hex(CASING), width: 8 * k }])),
        paths("lane-risk", memo("lane-risk", [runs, k], () => runs.map((r) => ({ path: r.path, color: hex(RISK_COLOR[r.status]), width: 4.5 * k })))),
      );
    }
  }

  // 5. Endpoints and stops of the selected lane.
  if (selected) {
    const pts = memo("endpoints", [selected, k], () => {
      const sh = selected.shipment;
      return [
        { p: [sh.origin.lon, sh.origin.lat] as LonLat, r: 6.5 * k },
        { p: [sh.destination.lon, sh.destination.lat] as LonLat, r: 6.5 * k },
        ...sh.stops.map((st) => ({ p: [st.lon, st.lat] as LonLat, r: 4.5 * k })),
      ];
    });
    layers.push(
      new ScatterplotLayer<(typeof pts)[number]>({
        id: "lane-endpoints",
        data: pts,
        getPosition: (d) => d.p,
        getRadius: (d) => d.r,
        radiusUnits: "pixels",
        getFillColor: hex("#ffffff"),
        getLineColor: hex(CASING),
        lineWidthUnits: "pixels",
        getLineWidth: 2.5,
        stroked: true,
        parameters: { depthCompare: "always" },
      }),
    );
  }

  // 6. Trucks at the scrubber time: the fleet as dots, the selected truck as a badge whose ring
  //    turns amber/red when the forecast at its position nears or crosses the cargo's limit.
  const trucks = memo("trucks", [s.entries, s.selectedId, s.run, s.time, s.nowTime, s.wind], () => truckStates(s));
  const fleetDots = memo("fleet-dots", [trucks], () => trucks.filter((t) => t.id !== s.selectedId));
  layers.push(
    new ScatterplotLayer<TruckState>({
      id: "fleet-trucks",
      data: fleetDots,
      getPosition: (d) => d.pos.lonlat,
      getRadius: (d) => (d.id === s.hoverId ? 7 : 5) * k,
      radiusUnits: "pixels",
      getFillColor: (d) => hex(MAP_HINT_COLOR[d.entry.hint]),
      getLineColor: hex(CASING),
      lineWidthUnits: "pixels",
      getLineWidth: 2,
      stroked: true,
      pickable: true,
      updateTriggers: { getRadius: s.hoverId },
      parameters: { depthCompare: "always" },
    }),
  );
  const me = trucks.find((t) => t.id === s.selectedId);
  if (me) {
    const ring = me.risk === "breach" ? RISK_COLOR.breach : me.risk === "near" ? RISK_COLOR.near : "#ffffff";
    const alarm = me.risk === "breach";
    const meData = memo("me", [me], () => [me]);
    layers.push(
      new ScatterplotLayer<TruckState>({
        id: "truck-halo",
        data: meData,
        getPosition: (d) => d.pos.lonlat,
        getRadius: 26 * k,
        radiusUnits: "pixels",
        radiusScale: alarm ? 0.85 + 0.35 * pulse : 0.9,
        getFillColor: hex(ring, alarm ? 90 : 38),
        updateTriggers: { getFillColor: `${ring}${alarm}` },
        parameters: { depthCompare: "always" },
      }),
      new IconLayer<TruckState>({
        id: "truck",
        data: meData,
        getPosition: (d) => d.pos.lonlat,
        getIcon: () => ({ id: `truck-${ring}`, url: TRUCK_ICON(ring), width: 96, height: 96, anchorY: 48, mask: false }),
        getSize: 36 * k,
        sizeUnits: "pixels",
        updateTriggers: { getIcon: ring },
        parameters: { depthCompare: "always" },
      }),
    );
  }
  return layers;
}

/** The render clock only needs to tick while something pulses or draws on. */
export function needsClock(s: Pick<SceneInput, "reducedMotion" | "selectedId">): boolean {
  return !s.reducedMotion && Boolean(s.selectedId);
}
