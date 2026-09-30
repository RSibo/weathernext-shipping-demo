// Camera framing shared by the Google map and the fallback deck.gl stage.

import { WebMercatorViewport } from "@deck.gl/core";
import { boundsOf, type Bounds, type LonLat } from "../lib/geo";
import { recommendedRoute, type RunState } from "../lib/runReducer";
import type { FleetEntry } from "../types";

export interface Padding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface Camera {
  lon: number;
  lat: number;
  /** deck.gl zoom (512 px tiles); Google Maps zoom is this + 1. */
  zoom: number;
}

/** What to frame: the selected lane with anything the agent drew for it, or every lane. */
export function frameBounds(target: "lane" | "fleet", entries: FleetEntry[], selectedId: string | undefined, run: RunState): Bounds | null {
  if (target === "fleet") return boundsOf(entries.flatMap((e) => (e.route ? [e.route.path] : [])));
  const entry = entries.find((e) => e.shipment.shipment_id === selectedId);
  const paths: LonLat[][] = [];
  const route = run.route ?? entry?.route;
  if (route) paths.push(route.path);
  const rec = recommendedRoute(run);
  if (rec) paths.push(rec.path);
  else for (const c of run.detour?.candidates ?? []) paths.push(c.route.path);
  if (!paths.length && entry) {
    const sh = entry.shipment;
    paths.push([[sh.origin.lon, sh.origin.lat], [sh.destination.lon, sh.destination.lat]]);
  }
  return boundsOf(paths);
}

export function fitCamera(b: Bounds, width: number, height: number, pad: Padding, maxZoom = 8.2): Camera {
  // Keep the padded box at least 120 px each way so small screens still get a sane fit.
  const w = Math.max(width, pad.left + pad.right + 120);
  const h = Math.max(height, pad.top + pad.bottom + 120);
  const vp = new WebMercatorViewport({ width: w, height: h }).fitBounds(
    [[b[0], b[1]], [b[2], b[3]]],
    { padding: pad, maxZoom },
  );
  return { lon: vp.longitude, lat: vp.latitude, zoom: vp.zoom };
}

/** Screen-space padding that keeps the framed content clear of the floating panels. */
export function panelPadding(width: number, stage: boolean): Padding {
  if (width < 1024) return { top: 24, right: 24, bottom: 24, left: 24 };
  // Panels are sized in rem (stage mode raises the root size), so pad in rem too; matches App.tsx.
  const rem = typeof document === "undefined" ? 16 : parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  // The legend floats over the top of the map: frame below it, with room for a chip above the route's top edge.
  const legend = typeof document === "undefined" ? null : document.querySelector<HTMLElement>('[data-testid="legend"]');
  const legendBottom = legend?.offsetHeight ? legend.offsetTop + legend.offsetHeight : 0;
  return { top: Math.max(3 * rem, legendBottom + 3.5 * rem), right: (stage ? 32 : 29.5) * rem + 24, bottom: 9 * rem, left: 22 * rem + 24 };
}

const easeInOut = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);

/** Animates between cameras in Web Mercator space (so the pan stays straight on screen). */
export function flyTo(from: Camera, to: Camera, ms: number, apply: (c: Camera) => void): () => void {
  if (ms <= 0) {
    apply(to);
    return () => {};
  }
  const merc = (c: Camera) => [((c.lon + 180) / 360), (1 - Math.log(Math.tan(Math.PI / 4 + (c.lat * Math.PI) / 360)) / Math.PI) / 2];
  const unmerc = (x: number, y: number) => [x * 360 - 180, (Math.atan(Math.exp(Math.PI * (1 - 2 * y))) * 360) / Math.PI - 90];
  const [x0, y0] = merc(from);
  const [x1, y1] = merc(to);
  // Zoom out a little mid-flight when the move is long, so the eye keeps its place.
  const dist = Math.hypot(x1 - x0, y1 - y0) * Math.pow(2, Math.min(from.zoom, to.zoom)) * 512;
  const bump = Math.min(1.2, Math.max(0, Math.log2(dist / 900)));
  const t0 = performance.now();
  let id = 0;
  const step = (now: number) => {
    const k = Math.min(1, (now - t0) / ms);
    const e = easeInOut(k);
    const [lon, lat] = unmerc(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e);
    apply({ lon, lat, zoom: from.zoom + (to.zoom - from.zoom) * e - bump * Math.sin(Math.PI * e) });
    if (k < 1) id = requestAnimationFrame(step);
  };
  id = requestAnimationFrame(step);
  return () => cancelAnimationFrame(id);
}
