import { Deck, FlyToInterpolator, MapView, WebMercatorViewport, type MapViewState } from "@deck.gl/core";
import { PathStyleExtension } from "@deck.gl/extensions";
import { PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import clsx from "clsx";
import { Clock3, Route, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { farthestFrom, positionAt, worstSegment } from "../lib/geo";
import { RISK_COLOR } from "../lib/palette";
import { recommendedRoute } from "../lib/runReducer";
import { selectEntry, selectRun, useStore } from "../store";
import { fitCamera, frameBounds, panelPadding } from "./camera";
import { pickHandlers } from "./DeckOverlay";
import { buildLayers, truckStates } from "./layers";
import { useScene } from "./useScene";

const dashedBorder = new PathStyleExtension({ dash: true });

// 1° graticule across Eastern & Southeastern Australia.
const GRATICULE = (() => {
  const lines: { path: [number, number][] }[] = [];
  for (let lon = 138; lon <= 156; lon += 1) lines.push({ path: [[lon, -40], [lon, -24]] });
  for (let lat = -40; lat <= -24; lat += 1) lines.push({ path: [[138, lat], [156, lat]] });
  return lines;
})();

// High-resolution Eastern Australia mainland + Tasmania polygon for dark vector basemap.
const AUSTRALIA_LAND: { polygon: [number, number][] }[] = [
  {
    polygon: [
      [138.0, -24.0],
      [151.9, -24.0],
      [152.7, -25.0],
      [153.15, -25.8],
      [153.12, -26.4],
      [153.18, -27.0],
      [153.43, -27.45],
      [153.55, -28.16],
      [153.63, -28.64],
      [153.35, -29.45],
      [153.14, -30.3],
      [153.08, -30.88],
      [152.92, -31.43],
      [152.55, -32.2],
      [152.18, -32.72],
      [151.78, -32.93],
      [151.57, -33.35],
      [151.28, -33.85],
      [151.18, -34.05],
      [150.92, -34.42],
      [150.82, -34.85],
      [150.55, -35.35],
      [150.23, -35.72],
      [150.1, -36.25],
      [149.98, -36.68],
      [149.95, -37.05],
      [149.97, -37.5],
      [149.3, -37.78],
      [148.5, -37.78],
      [147.8, -37.88],
      [147.15, -38.15],
      [146.42, -38.85],
      [145.9, -38.65],
      [145.45, -38.5],
      [144.95, -37.85],
      [144.65, -38.28],
      [143.9, -38.65],
      [143.55, -38.85],
      [142.48, -38.38],
      [141.0, -38.05],
      [139.8, -37.2],
      [138.6, -35.55],
      [138.0, -35.5],
      [138.0, -24.0],
    ],
  },
  // Tasmania (northern edge visible at wide zoom)
  {
    polygon: [
      [144.65, -40.65],
      [146.0, -41.05],
      [147.5, -40.85],
      [148.3, -41.0],
      [148.3, -42.5],
      [145.2, -42.5],
      [144.65, -40.65],
    ],
  },
];

// Australian state borders (QLD/NSW, NSW/VIC Murray River, NSW/SA & VIC/SA, ACT).
const STATE_BORDERS: { path: [number, number][] }[] = [
  // QLD / NSW border
  {
    path: [
      [141.0, -29.0],
      [148.95, -29.0],
      [149.4, -28.65],
      [150.35, -28.55],
      [151.18, -28.9],
      [151.95, -28.85],
      [152.45, -28.32],
      [153.2, -28.25],
      [153.55, -28.16],
    ],
  },
  // NSW / VIC border (Murray River -> Cape Howe)
  {
    path: [
      [141.0, -34.02],
      [142.16, -34.18],
      [143.35, -34.76],
      [144.75, -35.95],
      [145.65, -35.82],
      [146.91, -36.08],
      [147.9, -36.05],
      [148.18, -36.5],
      [148.24, -36.8],
      [149.97, -37.5],
    ],
  },
  // SA eastern border (141°E)
  {
    path: [
      [141.0, -26.0],
      [141.0, -38.05],
    ],
  },
  // ACT border
  {
    path: [
      [148.76, -35.15],
      [149.38, -35.15],
      [149.38, -35.85],
      [148.76, -35.85],
      [148.76, -35.15],
    ],
  },
];

interface CityLabel {
  name: string;
  lon: number;
  lat: number;
  major?: boolean;
  offset: [number, number];
}

const CITIES: CityLabel[] = [
  { name: "SYDNEY", lon: 151.2093, lat: -33.8688, major: true, offset: [10, 0] },
  { name: "MELBOURNE", lon: 144.9631, lat: -37.8136, major: true, offset: [0, 14] },
  { name: "BRISBANE", lon: 153.0251, lat: -27.4698, major: true, offset: [10, 0] },
  { name: "Canberra", lon: 149.13, lat: -35.2809, offset: [-10, 0] },
  { name: "Newcastle", lon: 151.7817, lat: -32.9283, offset: [10, 0] },
  { name: "Wollongong", lon: 150.8931, lat: -34.4278, offset: [10, 4] },
  { name: "Gold Coast", lon: 153.4, lat: -28.0167, offset: [10, 4] },
  { name: "Albury", lon: 146.9135, lat: -36.0737, offset: [-10, -6] },
  { name: "Goulburn", lon: 149.7186, lat: -34.7546, offset: [-10, -4] },
  { name: "Wagga Wagga", lon: 147.3598, lat: -35.1082, offset: [-10, -4] },
  { name: "Dubbo", lon: 148.6011, lat: -32.243, offset: [-10, 0] },
  { name: "Tamworth", lon: 150.929, lat: -31.0927, offset: [-10, 0] },
  { name: "Armidale", lon: 151.666, lat: -30.512, offset: [-10, 0] },
  { name: "Coffs Harbour", lon: 153.1157, lat: -30.2963, offset: [10, 0] },
  { name: "Port Macquarie", lon: 152.9089, lat: -31.4308, offset: [10, 0] },
  { name: "Bega", lon: 149.8417, lat: -36.6739, offset: [10, 4] },
  { name: "Sale", lon: 147.068, lat: -38.1065, offset: [0, 12] },
  { name: "Wangaratta", lon: 146.3125, lat: -36.3584, offset: [-10, 6] },
];

const STATE_LABELS = [
  { name: "NEW SOUTH WALES", lon: 146.2, lat: -32.6 },
  { name: "VICTORIA", lon: 144.2, lat: -36.8 },
  { name: "QUEENSLAND", lon: 148.2, lat: -26.8 },
];

/** Vector deck.gl map of Eastern Australia (Sydney · Brisbane · Melbourne) with projected route chips. */
export function FallbackStage() {
  const host = useRef<HTMLDivElement>(null);
  const deck = useRef<Deck<MapView> | null>(null);
  const scene = useScene(true);
  const request = useStore((s) => s.frameRequest);
  const entry = useStore(selectEntry);
  const run = useStore(selectRun);
  const time = useStore((s) => s.time);
  const wind = useStore((s) => s.forecasts.wind_mph);
  const stage = useStore((s) => s.stage);
  const playing = useStore((s) => s.playing);

  const [vs, setVs] = useState<{ longitude: number; latitude: number; zoom: number; width: number; height: number }>({
    longitude: 149.2,
    latitude: -33.2,
    zoom: 5.2,
    width: 1200,
    height: 800,
  });

  const view = (ms: number): MapViewState | null => {
    const s = useStore.getState();
    const b = frameBounds(s.frameTarget, s.fleet?.entries ?? [], s.selectedId, selectRun(s));
    const el = host.current;
    if (!b || !el) return null;
    const c = fitCamera(b, el.clientWidth, el.clientHeight, panelPadding(window.innerWidth, s.stage));
    return { longitude: c.lon, latitude: c.lat, zoom: c.zoom, ...(ms ? { transitionDuration: ms, transitionInterpolator: new FlyToInterpolator() } : {}) };
  };

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const init = view(0) ?? { longitude: 149.2, latitude: -33.2, zoom: 5.2 };
    setVs({
      longitude: init.longitude,
      latitude: init.latitude,
      zoom: init.zoom,
      width: el.clientWidth || 1200,
      height: el.clientHeight || 800,
    });
    deck.current = new Deck({
      parent: el,
      views: new MapView({ repeat: false }),
      initialViewState: init,
      controller: { dragRotate: false, touchRotate: false },
      onViewStateChange: ({ viewState }) => {
        const m = viewState as MapViewState;
        setVs({
          longitude: m.longitude,
          latitude: m.latitude,
          zoom: m.zoom,
          width: el.clientWidth || 1200,
          height: el.clientHeight || 800,
        });
      },
      ...pickHandlers(),
    });
    return () => {
      deck.current?.finalize();
      deck.current = null;
    };
  }, []);

  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const v = view(request === 0 || reduce ? 0 : 1400);
    if (v && request > 0) {
      deck.current?.setProps({ initialViewState: v });
      if (host.current) {
        setVs({
          longitude: v.longitude,
          latitude: v.latitude,
          zoom: v.zoom,
          width: host.current.clientWidth || 1200,
          height: host.current.clientHeight || 800,
        });
      }
    }
  }, [request]);

  useEffect(() => {
    if (!scene) return;
    deck.current?.setProps({
      layers: [
        new PolygonLayer({
          id: "au-landmass",
          data: AUSTRALIA_LAND,
          getPolygon: (d) => d.polygon,
          getFillColor: [24, 26, 28, 235],
          getLineColor: [82, 92, 106, 175],
          getLineWidth: 1.5,
          lineWidthUnits: "pixels",
          stroked: true,
          filled: true,
          parameters: { depthCompare: "always" },
        }),
        new PathLayer({
          id: "graticule",
          data: GRATICULE,
          getPath: (d) => d.path,
          getColor: [255, 255, 255, 13],
          getWidth: 1,
          widthUnits: "pixels",
          parameters: { depthCompare: "always" },
        }),
        new PathLayer({
          id: "au-state-borders",
          data: STATE_BORDERS,
          getPath: (d) => d.path,
          getColor: [160, 172, 188, 65],
          getWidth: 1.2,
          widthUnits: "pixels",
          extensions: [dashedBorder],
          getDashArray: [4, 3],
          parameters: { depthCompare: "always" },
        }),
        new TextLayer({
          id: "au-state-labels",
          data: STATE_LABELS,
          getPosition: (d) => [d.lon, d.lat],
          getText: (d) => d.name,
          getSize: 11,
          getColor: [160, 170, 182, 58],
          fontFamily: "Inter Variable, Inter, system-ui, sans-serif",
          fontWeight: 700,
          parameters: { depthCompare: "always" },
        }),
        new ScatterplotLayer<CityLabel>({
          id: "au-city-dots",
          data: CITIES,
          getPosition: (d) => [d.lon, d.lat],
          getRadius: (d) => (d.major ? 4.2 : 2.6),
          radiusUnits: "pixels",
          getFillColor: (d) => (d.major ? [230, 234, 240, 220] : [165, 175, 186, 155]),
          getLineColor: [13, 13, 13, 220],
          getLineWidth: 1.5,
          lineWidthUnits: "pixels",
          stroked: true,
          parameters: { depthCompare: "always" },
        }),
        new TextLayer<CityLabel>({
          id: "au-city-labels",
          data: CITIES,
          getPosition: (d) => [d.lon, d.lat],
          getText: (d) => d.name,
          getSize: (d) => (d.major ? 12 : 10.5),
          getColor: (d) => (d.major ? [236, 240, 245, 215] : [175, 184, 196, 165]),
          getPixelOffset: (d) => d.offset,
          getTextAnchor: (d) => (d.offset[0] > 0 ? "start" : d.offset[0] < 0 ? "end" : "middle"),
          getAlignmentBaseline: "center",
          fontFamily: "Inter Variable, Inter, system-ui, sans-serif",
          fontWeight: 600,
          outlineWidth: 2,
          outlineColor: [13, 13, 13, 220],
          fontSettings: { sdf: true },
          parameters: { depthCompare: "always" },
        }),
        ...buildLayers(scene),
      ],
    });
  });

  // Project HTML callout chips onto the WebMercatorViewport so FallbackStage has the same labels as GoogleStage.
  const vp = useMemo(() => {
    try {
      return new WebMercatorViewport({
        width: Math.max(1, vs.width),
        height: Math.max(1, vs.height),
        longitude: vs.longitude,
        latitude: vs.latitude,
        zoom: vs.zoom,
      });
    } catch {
      return null;
    }
  }, [vs]);

  const rec = recommendedRoute(run);
  const rerouted = run.advisory?.status === "REROUTE" && rec;
  const chosen = run.detour?.candidates.find((c) => c.route === rec);
  const segs = run.weather?.assessment.segments ?? entry?.assessment?.segments ?? [];
  const breach = !rerouted && run.advisory?.status !== "HOLD" ? worstSegment(segs) : undefined;
  const hold = run.advisory?.status === "HOLD" ? run.delay?.options.find((o) => o.option_id === run.advisory?.chosen_option_id) : undefined;
  const baseline = run.route ?? entry?.route;
  const recAt = useMemo(() => {
    if (!rerouted || !rec) return null;
    const worst = worstSegment(run.weather?.assessment.segments ?? []);
    const truck = worst ? positionAt(rec, (worst.start_t_s + worst.end_t_s) / 2).lonlat : undefined;
    return farthestFrom(rec.path, baseline?.path ?? [], truck);
  }, [rerouted, rec, baseline, run.weather]);

  const me = useMemo(
    () => (entry && wind ? truckStates({ entries: [entry], selectedId: entry.shipment.shipment_id, run, time, nowTime: wind.nowTime, wind })[0] : undefined),
    [entry, run, time, wind],
  );

  const project = (lon: number, lat: number): [number, number] | null => {
    if (!vp) return null;
    const [x, y] = vp.project([lon, lat]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return [x, y];
  };

  const breachXY = breach ? project(breach.peak_lon, breach.peak_lat) : null;
  const recXY = rerouted && recAt ? project(recAt[0], recAt[1]) : null;
  const holdXY = hold && entry ? project(entry.shipment.origin.lon, entry.shipment.origin.lat) : null;
  const truckXY = me && me.wind !== null && me.pos.state === "driving" && (playing || !run.advisory) ? project(me.pos.lonlat[0], me.pos.lonlat[1]) : null;
  const limit = entry?.shipment.wind_threshold_mph ?? 35;

  return (
    <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_55%_45%,#131820_0%,#0b0e12_75%)]">
      <div ref={host} className="absolute inset-0" data-testid="fallback-stage" />
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {breach && breachXY && (
          <div
            className={clsx("glass-chip absolute z-20 max-w-64 -translate-x-1/2 -translate-y-full rounded-control px-2.5 py-1.5", stage && "text-base")}
            style={{ left: breachXY[0], top: breachXY[1] - 14 }}
            data-testid="label-breach"
          >
            <div className="flex items-center gap-1.5 text-xs font-semibold text-hold-ink">
              <TriangleAlert className="size-3.5" aria-hidden />
              Over {limit} mph · p90 {breach.peak_wind}
            </div>
            <div className="tabular text-2xs text-ink-3">
              {breach.window} · near {breach.place}
            </div>
          </div>
        )}
        {rerouted && recXY && (
          <div
            className="glass-chip absolute z-20 -translate-x-1/2 -translate-y-full rounded-control px-2.5 py-1.5"
            style={{ left: recXY[0], top: recXY[1] - 14 }}
            data-testid="label-recommended"
          >
            <div className="flex items-center gap-1.5 text-xs font-semibold text-ink-1">
              <Route className="size-3.5 text-reroute-ink" aria-hidden />
              Recommended · {chosen?.label ?? rec!.description}
            </div>
            {chosen && <div className="tabular text-2xs text-ink-3">{[chosen.delta_duration, chosen.delta_distance].filter(Boolean).join(" · ")}</div>}
          </div>
        )}
        {hold && holdXY && (
          <div
            className="glass-chip absolute z-20 -translate-y-1/2 rounded-control px-2.5 py-1.5"
            style={{ left: holdXY[0] + 16, top: holdXY[1] }}
            data-testid="label-hold"
          >
            <div className="flex items-center gap-1.5 text-xs font-semibold text-ink-1">
              <Clock3 className="size-3.5 text-hold-ink" aria-hidden />
              Hold · depart {hold.depart}
            </div>
            <div className="text-2xs text-ink-3">{hold.shift} later, after the wind eases</div>
          </div>
        )}
        {me && me.wind !== null && truckXY && (
          <div
            className="glass-chip tabular absolute z-30 flex -translate-y-1/2 items-center gap-1.5 rounded-full py-1 pr-2.5 pl-2 text-xs font-semibold text-ink-1"
            style={{ left: truckXY[0] + (stage ? 32 : 24), top: truckXY[1] }}
            data-testid="label-truck"
          >
            <span className="size-2 rounded-full" style={{ background: RISK_COLOR[me.risk] }} aria-hidden />
            {Math.round(me.wind)} mph
            <span className="font-normal text-ink-4">here</span>
          </div>
        )}
      </div>
    </div>
  );
}
