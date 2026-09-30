#!/usr/bin/env node
/**
 * WCAG AA check of the UI's text tokens on every surface they sit on, read straight from
 * src/index.css so it cannot drift. Text needs 4.5:1; control edges 3:1. Exits 1 on any failure.
 *
 *   node scripts/contrast.mjs
 *
 * Glass panels are composited as drawn: surface-1 at 90% over the brightest forecast class, which
 * is itself drawn at 78% over the dark basemap. That is the brightest thing a panel ever covers.
 */
import fs from "node:fs";

const css = fs.readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const token = (name) => {
  const m = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token --color-${name} not found`);
  return m[1];
};
const rgb = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
const over = (a, b, t) => a.map((x, i) => Math.round(x * t + b[i] * (1 - t))); // a at opacity t over b
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const BASEMAP = rgb("#1d2433");
const s1 = rgb(token("surface-1"));
const glass = over(s1, over(rgb(token("field-4")), BASEMAP, 0.78), 0.9);
const surfaces = {
  "surface-0": rgb(token("surface-0")), "surface-1": s1, "surface-2": rgb(token("surface-2")), "surface-3": rgb(token("surface-3")),
  "glass (worst)": glass, "card/70 on glass": over(rgb(token("surface-2")), glass, 0.7),
};
const text = ["ink-1", "ink-2", "ink-3", "ink-4", "accent-ink", "clear-ink", "monitor-ink", "reroute-ink", "hold-ink"];

const rows = [];
for (const t of text) for (const [s, bg] of Object.entries(surfaces)) rows.push({ fg: t, bg: s, min: 4.5, ratio: ratio(rgb(token(t)), bg) });
for (const k of ["clear", "monitor", "reroute", "hold"]) {
  rows.push({ fg: `${k}-ink`, bg: `${k}/12 chip on glass`, min: 4.5, ratio: ratio(rgb(token(`${k}-ink`)), over(rgb(token(k)), glass, 0.12)) });
}
for (const b of ["accent", "accent-hover"]) rows.push({ fg: "white", bg: `${b} (primary button)`, min: 4.5, ratio: ratio([255, 255, 255], rgb(token(b))) });
for (const [s, bg] of Object.entries(surfaces)) rows.push({ fg: "line-control", bg: s, min: 3, ratio: ratio(rgb(token("line-control")), bg) });

const fails = rows.filter((r) => r.ratio < r.min);
const worst = [...rows].sort((a, b) => a.ratio / a.min - b.ratio / b.min).slice(0, 5);
console.table(worst.map((r) => ({ ...r, ratio: r.ratio.toFixed(2) })));
console.log(`${rows.length} pairs checked, ${fails.length} below AA${fails.length ? ":" : ""}`);
for (const f of fails) console.log(`  ✗ ${f.fg} on ${f.bg}: ${f.ratio.toFixed(2)} < ${f.min}`);
process.exit(fails.length ? 1 : 0);
