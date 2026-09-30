import { Deck, FlyToInterpolator, MapView, type MapViewState } from "@deck.gl/core";
import { PathLayer } from "@deck.gl/layers";
import { MapPinOff } from "lucide-react";
import { useEffect, useRef } from "react";
import { selectRun, useStore } from "../store";
import { fitCamera, frameBounds, panelPadding } from "./camera";
import { pickHandlers } from "./DeckOverlay";
import { buildLayers } from "./layers";
import { useScene } from "./useScene";

// A faint 1° graticule stands in for the basemap so distances still read.
const GRATICULE = (() => {
  const lines: { path: [number, number][] }[] = [];
  for (let lon = -125; lon <= -90; lon += 1) lines.push({ path: [[lon, 25], [lon, 50]] });
  for (let lat = 25; lat <= 50; lat += 1) lines.push({ path: [[-125, lat], [-90, lat]] });
  return lines;
})();

/** The same scene on a plain deck.gl canvas, for when Google Maps can't load (key, network). */
export function FallbackStage() {
  const host = useRef<HTMLDivElement>(null);
  const deck = useRef<Deck<MapView> | null>(null);
  const scene = useScene(true);
  const request = useStore((s) => s.frameRequest);

  const view = (ms: number): MapViewState | null => {
    const s = useStore.getState();
    const b = frameBounds(s.frameTarget, s.fleet?.entries ?? [], s.selectedId, selectRun(s));
    const el = host.current;
    if (!b || !el) return null;
    const c = fitCamera(b, el.clientWidth, el.clientHeight, panelPadding(window.innerWidth, s.stage));
    return { longitude: c.lon, latitude: c.lat, zoom: c.zoom, ...(ms ? { transitionDuration: ms, transitionInterpolator: new FlyToInterpolator() } : {}) };
  };

  useEffect(() => {
    if (!host.current) return;
    deck.current = new Deck({
      parent: host.current,
      views: new MapView({ repeat: false }),
      initialViewState: view(0) ?? { longitude: -108, latitude: 40, zoom: 5 },
      controller: { dragRotate: false, touchRotate: false },
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
    if (v && request > 0) deck.current?.setProps({ initialViewState: v });
  }, [request]);

  useEffect(() => {
    if (!scene) return;
    deck.current?.setProps({
      layers: [
        new PathLayer({ id: "graticule", data: GRATICULE, getPath: (d) => d.path, getColor: [255, 255, 255, 14], getWidth: 1, widthUnits: "pixels" }),
        ...buildLayers(scene),
      ],
    });
  });

  return (
    <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_40%,#1a1a19_0%,#0d0d0d_70%)]">
      <div ref={host} className="absolute inset-0" data-testid="fallback-stage" />
      <div className="glass-chip pointer-events-none absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full px-3 py-1 text-2xs text-ink-3">
        <MapPinOff className="size-3.5" aria-hidden /> Basemap unavailable: showing forecast and routes only
      </div>
    </div>
  );
}
