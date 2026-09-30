import { create } from "zustand";
import { api, BufferedStreamError, HttpError, listRun, playEvents, streamRun } from "./lib/api";
import { decodeForecast, type Forecast } from "./lib/field";
import { worstSegment } from "./lib/geo";
import { IDLE, isActive, runReducer, type RunAction, type RunState } from "./lib/runReducer";
import { epoch } from "./lib/time";
import { presets } from "./lib/presets";
import type { AppConfig, FieldVar, Fleet, RunEvent, Source } from "./types";

export type Tab = "trace" | "advisory" | "details";

/** Story hours per real second while the time scrubber plays. */
export const PLAY_RATE_H_PER_S = 0.75;

interface AppStore {
  boot: "loading" | "ready" | "error";
  bootError?: string;
  config?: AppConfig;
  fleet?: Fleet;
  mode?: Source;
  forecasts: Partial<Record<FieldVar, Forecast>>;
  fieldVar: FieldVar;
  selectedId?: string;
  hoverId?: string;
  runs: Record<string, RunState>;
  time: number; // epoch seconds on the forecast timeline
  playing: boolean;
  tab: Tab;
  stage: boolean;
  attract: boolean;
  help: boolean;
  mapsFailed: boolean;
  toast?: { id: number; text: string; tone?: "good" | "neutral" };
  /** Bumped to ask the map to re-frame; the target says what to fit. */
  frameRequest: number;
  frameTarget: "lane" | "fleet";
  scanning: boolean;

  start: () => Promise<void>;
  select: (id: string) => void;
  hover: (id?: string) => void;
  runAgent: (id?: string, opts?: { golden?: boolean; speed?: number }) => Promise<void>;
  stopRun: () => void;
  setTime: (t: number) => void;
  animateTimeTo: (t: number, ms?: number) => void;
  togglePlay: (on?: boolean) => void;
  setTab: (tab: Tab) => void;
  setFieldVar: (v: FieldVar) => Promise<void>;
  setAttract: (on: boolean) => void;
  setHelp: (on: boolean) => void;
  setMapsFailed: () => void;
  showToast: (text: string, tone?: "good" | "neutral") => void;
  reframe: (target?: "lane" | "fleet") => void;
  rescan: () => Promise<void>;
}

let runCtrl: AbortController | null = null;
let timeAnim = 0;
let playAnim = 0;
let tabTimer: ReturnType<typeof setTimeout> | undefined;

export const useStore = create<AppStore>((set, get) => {
  const dispatch = (id: string, action: RunAction) =>
    set((s) => ({ runs: { ...s.runs, [id]: runReducer(s.runs[id] ?? IDLE, action) } }));

  const timeRange = (): [number, number] => {
    const f = get().forecasts.wind_mph;
    return f ? [f.initTime, f.times[f.times.length - 1]] : [0, 0];
  };

  /** The storytelling beats that follow run events: time jumps to the worst of the storm, then the verdict. */
  const react = (id: string, e: RunEvent) => {
    if (get().selectedId !== id) return;
    // A breach, or failing that the closest call (MONITOR): either way the truck drives into it.
    const hot = e.type === "weather_checked" ? (e.assessment.segments.length ? e.assessment.segments : e.assessment.near_segments) : [];
    if (hot.length > 0) {
      const entry = get().fleet?.entries.find((x) => x.shipment.shipment_id === id);
      const depart = epoch(entry?.shipment.depart_iso ?? get().fleet?.now_iso, get().time);
      const worst = worstSegment(hot)!;
      setTimeout(() => get().animateTimeTo(depart + (worst.start_t_s + worst.end_t_s) / 2, 1800), 350);
    }
    if (e.type === "detour_computed") get().reframe();
    if (e.type === "advisory") {
      clearTimeout(tabTimer);
      tabTimer = setTimeout(() => get().tab === "trace" && set({ tab: "advisory" }), 1400);
    }
  };

  return {
    boot: "loading",
    forecasts: {},
    fieldVar: "wind_mph",
    runs: {},
    time: 0,
    playing: false,
    tab: "trace",
    stage: presets.stage,
    attract: false,
    help: false,
    mapsFailed: false,
    frameRequest: 0,
    frameTarget: "lane",
    scanning: false,

    start: async () => {
      set({ boot: "loading", bootError: undefined });
      try {
        const config = await api.config();
        const mode = presets.mode && config.modes.some((m) => m.id === presets.mode) ? presets.mode : config.default_mode;
        const [fleet, wind] = await Promise.all([api.fleet(mode), api.forecast("wind_mph", mode)]);
        const forecast = decodeForecast(wind);
        const ids = fleet.entries.map((e) => e.shipment.shipment_id);
        const selectedId = presets.shipment && ids.includes(presets.shipment) ? presets.shipment : ids.includes(config.hero_shipment) ? config.hero_shipment : ids[0];
        const t0 = presets.hours !== undefined ? forecast.nowTime + presets.hours * 3600 : forecast.nowTime;
        set({
          config, fleet, mode, forecasts: { wind_mph: forecast }, selectedId, boot: "ready",
          time: Math.min(Math.max(t0, forecast.initTime), forecast.times[forecast.times.length - 1]),
          tab: presets.tab ?? "trace",
        });
      } catch (err) {
        set({ boot: "error", bootError: err instanceof Error ? err.message : String(err) });
      }
    },

    select: (id) => {
      if (get().selectedId === id) return get().reframe();
      set((s) => ({
        selectedId: id,
        tab: s.runs[id]?.advisory && s.tab !== "details" ? "advisory" : s.tab === "details" ? "details" : "trace",
        frameRequest: s.frameRequest + 1,
        frameTarget: "lane",
      }));
    },
    hover: (id) => set({ hoverId: id }),

    runAgent: async (id, opts = {}) => {
      const shipmentId = id ?? get().selectedId;
      if (!shipmentId) return;
      runCtrl?.abort();
      const ctrl = new AbortController();
      runCtrl = ctrl;
      if (get().selectedId !== shipmentId) get().select(shipmentId);
      set({ tab: "trace" });
      const golden = Boolean(opts.golden);
      dispatch(shipmentId, { kind: "connect", transport: golden ? "golden" : "live", now: performance.now() });
      const onEvent = (e: RunEvent) => {
        if (ctrl.signal.aborted) return;
        dispatch(shipmentId, { kind: "event", event: e, now: performance.now() });
        react(shipmentId, e);
      };
      const hasGolden = get().config?.golden.includes(shipmentId) ?? false;
      try {
        try {
          await streamRun({
            shipmentId, mode: get().mode, golden, speed: opts.speed ?? 1, signal: ctrl.signal, onEvent,
            onOpen: (transport) => dispatch(shipmentId, { kind: "connect", transport, now: performance.now() }),
            firstEventTimeoutMs: presets.nostream ? 1 : 8000,
          });
        } catch (err) {
          if (!(err instanceof BufferedStreamError) || golden) throw err;
          // A proxy is buffering the stream: fetch the same run as one list and pace it locally.
          dispatch(shipmentId, { kind: "connect", transport: "list", now: performance.now() });
          const events = await listRun(shipmentId, get().mode, ctrl.signal);
          await playEvents(events, { signal: ctrl.signal, onEvent });
        }
        const run = get().runs[shipmentId];
        if (!ctrl.signal.aborted && run && isActive(run)) {
          dispatch(shipmentId, { kind: "fail", failure: { code: "incomplete", message: "The run ended early.", fallback: hasGolden ? "golden" : undefined } });
        }
      } catch (err) {
        if (ctrl.signal.aborted) {
          dispatch(shipmentId, { kind: "cancel" });
          return;
        }
        const failure =
          err instanceof HttpError && err.status === 429
            ? { code: "capped", message: err.message }
            : err instanceof HttpError
              ? { code: `http_${err.status}`, message: err.message, fallback: hasGolden && !golden ? ("golden" as const) : undefined }
              : { code: "network", message: "The connection dropped mid-run.", fallback: hasGolden && !golden ? ("golden" as const) : undefined };
        dispatch(shipmentId, { kind: "fail", failure });
      } finally {
        if (runCtrl === ctrl) runCtrl = null;
      }
    },

    stopRun: () => runCtrl?.abort(),

    setTime: (t) => {
      cancelAnimationFrame(timeAnim);
      const [lo, hi] = timeRange();
      set({ time: Math.min(Math.max(t, lo), hi) });
    },

    animateTimeTo: (target, ms = 1200) => {
      cancelAnimationFrame(timeAnim);
      get().togglePlay(false);
      const [lo, hi] = timeRange();
      const to = Math.min(Math.max(target, lo), hi);
      const from = get().time;
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (reduce || ms <= 0) return set({ time: to });
      const t0 = performance.now();
      const step = (now: number) => {
        const k = Math.min(1, (now - t0) / ms);
        const eased = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
        set({ time: from + (to - from) * eased });
        if (k < 1) timeAnim = requestAnimationFrame(step);
      };
      timeAnim = requestAnimationFrame(step);
    },

    togglePlay: (on) => {
      const playing = on ?? !get().playing;
      cancelAnimationFrame(playAnim);
      if (!playing) return set({ playing: false });
      cancelAnimationFrame(timeAnim);
      const [lo, hi] = timeRange();
      if (get().time >= hi - 60) set({ time: lo });
      set({ playing: true });
      let last = performance.now();
      const step = (now: number) => {
        const dt = (now - last) / 1000;
        last = now;
        const t = get().time + dt * PLAY_RATE_H_PER_S * 3600;
        if (t >= hi) return set({ time: hi, playing: false });
        set({ time: t });
        playAnim = requestAnimationFrame(step);
      };
      playAnim = requestAnimationFrame(step);
    },

    setTab: (tab) => {
      clearTimeout(tabTimer);
      set({ tab });
    },

    setFieldVar: async (v) => {
      if (!get().forecasts[v]) {
        try {
          const f = decodeForecast(await api.forecast(v, get().mode));
          set((s) => ({ forecasts: { ...s.forecasts, [v]: f } }));
        } catch {
          return get().showToast("That forecast layer is unavailable right now.");
        }
      }
      set({ fieldVar: v });
    },

    setAttract: (on) => set({ attract: on }),
    setHelp: (on) => set({ help: on }),
    setMapsFailed: () => set({ mapsFailed: true }),
    showToast: (text, tone = "neutral") => set({ toast: { id: Date.now(), text, tone } }),
    reframe: (target = "lane") => set((s) => ({ frameRequest: s.frameRequest + 1, frameTarget: target })),

    rescan: async () => {
      if (get().scanning) return;
      set({ scanning: true });
      get().reframe("fleet");
      const t0 = performance.now();
      try {
        const fleet = await api.fleet(get().mode);
        // Hold the sweep long enough to read as a scan, even when the cache answers instantly.
        await new Promise((r) => setTimeout(r, Math.max(0, 900 - (performance.now() - t0))));
        set({ fleet });
      } catch {
        get().showToast("The fleet scan failed; showing the last result.");
      } finally {
        set({ scanning: false });
      }
    },
  };
});

export const selectRun = (s: AppStore): RunState => (s.selectedId && s.runs[s.selectedId]) || IDLE;
export const selectEntry = (s: AppStore) => s.fleet?.entries.find((e) => e.shipment.shipment_id === s.selectedId);
