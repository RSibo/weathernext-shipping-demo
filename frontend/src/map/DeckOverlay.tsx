import type { PickingInfo } from "@deck.gl/core";
import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import { useMap } from "@vis.gl/react-google-maps";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { buildLayers, type TruckState } from "./layers";
import { useScene } from "./useScene";

/** Truck dots are pickable: hover lifts the lane, click selects the truck. */
export function pickHandlers() {
  const { select, hover } = useStore.getState();
  return {
    onClick: (info: PickingInfo) => {
      const t = info.object as TruckState | undefined;
      if (t?.id) select(t.id);
    },
    onHover: (info: PickingInfo) => {
      const t = info.object as TruckState | undefined;
      const id = t?.id;
      if (useStore.getState().hoverId !== id) hover(id);
    },
  };
}

/** deck.gl's container inside the map div (see @deck.gl/google-maps utils); finalize() leaves it behind. */
const DECK_CONTAINER = "#deck-gl-google-maps-container";

/**
 * deck.gl drawn into the Google vector map's own WebGL context (interleaved), so map labels sit
 * above the routes. Where Google can't get a good WebGL context (software GL, blocklisted GPUs) it
 * boots as VECTOR and then falls back to RASTER; deck.gl doesn't survive that flip (its old container
 * stays and the new canvas lands offset inside it), so the overlay attaches only once the map has
 * gone idle with its rendering type settled, and is rebuilt from scratch if it ever changes again.
 */
export function DeckOverlay() {
  const map = useMap();
  const overlay = useRef<GoogleMapsOverlay | null>(null);
  const scene = useScene(true);
  const [rendering, setRendering] = useState<string>();

  useEffect(() => {
    if (!map) return;
    let settled = false;
    const settle = () => {
      settled = true;
      setRendering(map.getRenderingType());
    };
    const idle = map.addListener("idle", () => {
      idle.remove();
      settle();
    });
    // Tiles that never load (offline, quota) shouldn't keep the routes off the map.
    const timer = setTimeout(settle, 5000);
    const changed = map.addListener("renderingtype_changed", () => settled && setRendering(map.getRenderingType()));
    return () => {
      clearTimeout(timer);
      idle.remove();
      changed.remove();
    };
  }, [map]);

  // Created in the effect (not render) so StrictMode's mount/unmount/mount leaves one live overlay.
  useEffect(() => {
    if (!map || !rendering || rendering === "UNINITIALIZED") return;
    const div = map.getDiv?.();
    if (!div) {
      useStore.getState().setMapsFailed();
      return;
    }
    div.querySelector(DECK_CONTAINER)?.remove();
    const o = new GoogleMapsOverlay({ interleaved: true, ...pickHandlers() });
    o.setMap(map);
    overlay.current = o;
    if (import.meta.env.DEV) (window as unknown as { __overlay?: GoogleMapsOverlay }).__overlay = o;
    return () => {
      o.finalize();
      map.getDiv?.()?.querySelector(DECK_CONTAINER)?.remove();
      overlay.current = null;
    };
  }, [map, rendering]);
  useEffect(() => {
    if (scene) overlay.current?.setProps({ layers: buildLayers(scene) });
  });
  return null;
}
