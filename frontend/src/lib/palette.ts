// Colour checks (dataviz validate_palette.js, OKLab ΔE×100; scripts/contrast.mjs for WCAG):
// - FIELD_RAMP passes as an ordinal ramp on the dark panel (one hue, monotone, light end 2.15:1).
// - Map risk: amber vs red is ΔE 24.4 under deutan simulation, 28.4 normal, both 3:1+ on the basemap.
// - Status colours are reserved for the four decisions and always ship with an icon + label: red vs
//   green is only ΔE 4.1 for deutan viewers, so they never share the map, and REROUTE vs MONITOR is
//   ΔE 13.6 even with normal vision, so their chips differ by icon and word, never by colour alone.
// - STATUS_INK tints are for small text on dark panels; each is 4.5:1+ on every surface.

import type { Hint, SampleStatus, Status } from "../types";

export type RGBA = [number, number, number, number];

export function hex(h: string, alpha = 255): RGBA {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha];
}

export const INK = "#e6e8eb";
export const CASING = "#0d0d0d";
export const MUTED = "#898781";

/** Forecast field: one hue, light = more wind, classed at the decision thresholds. */
export const FIELD_RAMP = ["#184f95", "#2a78d6", "#6da7ec", "#cde2fb"] as const;
export const FIELD_ALPHA = [0.42, 0.56, 0.68, 0.78] as const;

export const STATUS_COLOR: Record<Status, string> = {
  CLEAR: "#0ca30c",
  MONITOR: "#fab219",
  REROUTE: "#ec835a",
  HOLD: "#d03b3b",
};

/** Text-safe tints of the status colours for small type on dark panels. */
export const STATUS_INK: Record<Status, string> = {
  CLEAR: "#4ade80",
  MONITOR: "#fbbf24",
  REROUTE: "#f19a78",
  HOLD: "#ff8a8a",
};

export const HINT_COLOR: Record<Hint, string> = {
  CLEAR: STATUS_COLOR.CLEAR,
  MONITOR: STATUS_COLOR.MONITOR,
  ACTION: STATUS_COLOR.HOLD,
  UNKNOWN: MUTED,
};

/** Fleet dots on the map: clear is neutral ink, so red and green never meet on the map. */
export const MAP_HINT_COLOR: Record<Hint, string> = {
  CLEAR: INK,
  MONITOR: STATUS_COLOR.MONITOR,
  ACTION: STATUS_COLOR.HOLD,
  UNKNOWN: MUTED,
};

/** Route risk: ink where clear, amber within 15% of the limit, red where it breaches. */
export const RISK_COLOR: Record<SampleStatus, string> = {
  clear: INK,
  near: "#fab219",
  breach: "#d03b3b",
  unmonitored: MUTED,
};
