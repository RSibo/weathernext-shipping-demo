#!/usr/bin/env node
/**
 * Visual verification: screenshots the running app across viewports and states so UI changes are
 * checked by looking at them. Adapted from k8s-copilot's frontend/scripts/screenshot.mjs.
 *
 *   node scripts/shots.mjs                          # the whole matrix → screenshots/
 *   node scripts/shots.mjs --only=hero-done,phone   # a subset (substring match on names)
 *   node scripts/shots.mjs --url="/?shipment=SHP-2003" --out=/tmp/x.png --viewport=1440x900
 *
 * Needs a Chrome with WebGL: CHROMIUM_PATH (else Playwright's cached Chromium) runs with SwiftShader.
 * Console errors and page errors are printed; --strict exits non-zero if any occurred.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer from "puppeteer-core";

const CANDIDATES = [
  process.env.CHROMIUM_PATH,
  path.join(os.homedir(), ".cache/ms-playwright/chromium-1117/chrome-linux/chrome"),
  "/usr/bin/chromium",
  "/usr/bin/google-chrome",
].filter(Boolean);
const CHROMIUM_PATH = CANDIDATES.find((p) => fs.existsSync(p));

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.length ? v.join("=") : true];
  }),
);
const BASE = args.base ?? "http://localhost:3000";
const OUT = args.dir ?? "screenshots";

const STAGE = "1920x1080";
const LAPTOP = "1440x900";
const PHONE = "390x844";

// name, query, viewport, wait: "ready" | "done" | ms, then optional keys / filter.
const MATRIX = [
  { name: "fleet-idle", q: "?attract=0", vp: LAPTOP },
  { name: "fleet-idle-stage", q: "?attract=0&stage=1", vp: STAGE, wait: 3000 }, // 1080p raster tiles paint late under SwiftShader
  { name: "hero-mid", q: "?attract=0&shipment=SHP-2001&golden=1", vp: LAPTOP, wait: 7000, noDone: true },
  { name: "hero-done", q: "?attract=0&shipment=SHP-2001&golden=8", vp: LAPTOP, wait: "done" },
  { name: "hero-done-stage", q: "?attract=0&stage=1&shipment=SHP-2001&golden=8", vp: STAGE, wait: "done" },
  { name: "hero-trace", q: "?attract=0&shipment=SHP-2001&golden=8", vp: LAPTOP, wait: "done", click: "Trace" },
  { name: "hero-details", q: "?attract=0&shipment=SHP-2001&golden=8", vp: LAPTOP, wait: "done", click: "Details" },
  { name: "monitor", q: "?attract=0&shipment=SHP-2002&golden=8", vp: LAPTOP, wait: "done" },
  { name: "hold", q: "?attract=0&shipment=SHP-2003&golden=8", vp: LAPTOP, wait: "done" },
  { name: "clear", q: "?attract=0&shipment=SHP-2004&golden=8", vp: LAPTOP, wait: "done" },
  { name: "phone", q: "?attract=0&shipment=SHP-2001&golden=8", vp: PHONE, wait: "done", full: true },
  { name: "help", q: "?attract=0", vp: LAPTOP, keys: ["?"] },
  { name: "hero-gray", q: "?attract=0&shipment=SHP-2001&golden=8", vp: LAPTOP, wait: "done", filter: "grayscale(1)" },
  { name: "hero-deutan", q: "?attract=0&shipment=SHP-2001&golden=8", vp: LAPTOP, wait: "done", filter: "url(#deutan)" },
];

// Deuteranopia (Machado et al. 2009, severity 1.0) as an SVG colour matrix.
const DEUTAN_SVG = `<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute;width:0;height:0"><filter id="deutan"><feColorMatrix type="matrix" values="0.367 0.861 -0.228 0 0  0.280 0.673 0.047 0 0  -0.012 0.043 0.969 0 0  0 0 0 1 0"/></filter></svg>`;

/** Software GL only: Google falls back to a raster map and SwiftShader reports stalls. Real GPUs get vector. */
const HEADLESS_NOISE = [/Attempted to load a Vector Map/, /is not a vector map/, /GL Driver Message/];

async function shoot(browser, shot, outPath) {
  const [w, h] = shot.vp.split("x").map(Number);
  const page = await browser.newPage();
  const problems = [];
  page.on("console", (m) => {
    if (m.type() !== "error" && m.type() !== "warning") return;
    if (HEADLESS_NOISE.some((re) => re.test(m.text()))) return;
    problems.push(`[console.${m.type()}] ${m.text().slice(0, 300)}`);
  });
  page.on("pageerror", (e) => problems.push(`[pageerror] ${e.message}`));
  page.on("requestfailed", (r) => {
    const u = r.url();
    // Aborted module fetches are the page closing mid-load, not failures.
    if (r.failure()?.errorText === "net::ERR_ABORTED") return;
    if (!u.includes("google") && !u.includes("gstatic")) problems.push(`[requestfailed] ${u} ${r.failure()?.errorText}`);
  });
  await page.setViewport({ width: w, height: h, isMobile: w < 768, hasTouch: w < 768, deviceScaleFactor: w < 768 ? 2 : 1 });
  const t0 = Date.now();
  await page.goto(BASE + shot.q, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector('[data-testid="scrubber"]', { timeout: 20000 }).catch(() => problems.push("[warn] app never became ready"));
  // Map tiles + first deck frame.
  await new Promise((r) => setTimeout(r, 2500));
  if (shot.wait === "done") {
    await page
      .waitForFunction(() => ["done", "error"].includes(document.querySelector('[data-testid="run-phase"]')?.dataset.phase ?? ""), { timeout: 60000 })
      .catch(() => problems.push("[warn] run never finished"));
    await new Promise((r) => setTimeout(r, 2200)); // advisory tab switch + draw-on
  } else if (typeof shot.wait === "number") {
    await new Promise((r) => setTimeout(r, shot.wait));
  }
  if (shot.click) {
    await page.evaluate((text) => {
      const el = [...document.querySelectorAll("button,[role=tab]")].find((b) => b.textContent.trim().startsWith(text));
      el?.click();
    }, shot.click);
    await new Promise((r) => setTimeout(r, 600));
  }
  for (const k of shot.keys ?? []) {
    // Named keys ("ArrowRight", "Space") are pressed; anything else is typed.
    if (k.length > 1) await page.keyboard.press(k);
    else await page.keyboard.type(k);
    await new Promise((r) => setTimeout(r, 500));
  }
  if (shot.filter) {
    await page.evaluate(
      (f, svg) => {
        document.body.insertAdjacentHTML("beforeend", svg);
        document.documentElement.style.filter = f;
      },
      shot.filter,
      DEUTAN_SVG,
    );
    await new Promise((r) => setTimeout(r, 400));
  }
  await new Promise((r) => setTimeout(r, Number(args.settle ?? 400)));
  await page.screenshot({ path: outPath, fullPage: Boolean(shot.full) });
  await page.close();
  return { ms: Date.now() - t0, problems };
}

const browser = await puppeteer.launch({
  executablePath: CHROMIUM_PATH,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-gl=angle", "--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});

let failed = false;
try {
  let shots = MATRIX;
  if (args.url) shots = [{ name: "adhoc", q: args.url, vp: args.viewport ?? LAPTOP, wait: args.wait === "done" ? "done" : Number(args.wait ?? 0), keys: args.keys ? String(args.keys).split(",") : [], click: args.click, full: Boolean(args.full) }];
  else if (args.only) {
    const want = String(args.only).split(",");
    shots = MATRIX.filter((s) => want.some((w) => s.name.includes(w)));
  }
  fs.mkdirSync(OUT, { recursive: true });
  for (const shot of shots) {
    const out = args.out && shots.length === 1 ? String(args.out) : shot.name;
    const outPath = out.includes("/") ? out : path.join(OUT, out.endsWith(".png") ? out : `${out}.png`);
    const { ms, problems } = await shoot(browser, shot, outPath);
    console.log(`${problems.length ? "!" : "✓"} ${shot.name.padEnd(18)} ${shot.vp.padEnd(10)} ${(ms / 1000).toFixed(1)}s → ${outPath}`);
    for (const p of problems) console.log(`    ${p}`);
    if (problems.some((p) => !p.startsWith("[console.warning]"))) failed = true;
  }
} finally {
  await browser.close();
}
if (failed && args.strict) process.exit(1);
