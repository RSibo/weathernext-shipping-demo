// URL presets for the presenter and for screenshots, e.g.
//   /?shipment=SHP-2001&golden=1&stage=1      hero run, recorded, projector sizing
//   /?shipment=SHP-2003&t=2.5&tab=details     a lane, 2.5 story hours after "now"
// Unknown values are ignored rather than failing the page.

import type { Source } from "../types";

type Tab = "trace" | "advisory" | "details";

export interface Presets {
  shipment?: string;
  mode?: Source;
  /** Story hours after "now" to open the time scrubber at. */
  hours?: number;
  tab?: Tab;
  stage: boolean;
  /** Start the recorded run for the selected shipment once loaded (speed via golden=4). */
  golden?: number;
  /** Start a live agent run for the selected shipment once loaded. */
  run: boolean;
  /** Idle attract mode; on by default only on the public URL. */
  attract: boolean;
  /** Force the ?stream=0 path (proxy-buffering drill). */
  nostream: boolean;
  /** Hide transient chrome (help hint, toasts) for clean screenshots. */
  clean: boolean;
}

export function parsePresets(search: string): Presets {
  const p = new URLSearchParams(search);
  const flag = (k: string) => p.get(k) === "1" || p.get(k) === "true";
  const num = (k: string) => {
    const v = p.get(k);
    if (v === null || v === "") return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  };
  const shipment = p.get("shipment")?.toUpperCase();
  const mode = p.get("mode");
  const tab = p.get("tab");
  const golden = p.get("golden");
  return {
    shipment: shipment && /^[A-Z0-9-]{1,32}$/.test(shipment) ? shipment : undefined,
    mode: mode === "live" || mode === "replay" || mode === "scenario" ? mode : undefined,
    hours: num("t"),
    tab: tab === "trace" || tab === "advisory" || tab === "details" ? tab : undefined,
    stage: flag("stage"),
    golden: golden === null ? undefined : golden === "1" || golden === "true" ? 1 : Math.min(16, Math.max(0, Number(golden) || 1)),
    run: flag("run"),
    attract: p.get("attract") !== "0",
    nostream: flag("nostream"),
    clean: flag("clean"),
  };
}

export const presets: Presets = parsePresets(typeof window === "undefined" ? "" : window.location.search);
