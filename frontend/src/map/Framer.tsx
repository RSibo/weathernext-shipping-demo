import { useMap } from "@vis.gl/react-google-maps";
import { useEffect, useRef } from "react";
import { selectRun, useStore } from "../store";
import { fitCamera, flyTo, frameBounds, panelPadding, type Camera } from "./camera";

/** Moves the Google camera whenever the store asks for a frame (select, detour found, `0`). */
export function Framer() {
  const map = useMap();
  const request = useStore((s) => s.frameRequest);
  const first = useRef(true);

  useEffect(() => {
    if (!map) return;
    const s = useStore.getState();
    const b = frameBounds(s.frameTarget, s.fleet?.entries ?? [], s.selectedId, selectRun(s));
    if (!b) return;
    const div = map.getDiv();
    const to = fitCamera(b, div.clientWidth, div.clientHeight, panelPadding(window.innerWidth, s.stage));
    const c = map.getCenter();
    const from: Camera = { lon: c?.lng() ?? to.lon, lat: c?.lat() ?? to.lat, zoom: (map.getZoom() ?? to.zoom + 1) - 1 };
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const ms = first.current || reduce ? 0 : 1400;
    first.current = false;
    return flyTo(from, to, ms, (cam) => map.moveCamera({ center: { lng: cam.lon, lat: cam.lat }, zoom: cam.zoom + 1 }));
  }, [map, request]);
  return null;
}
