import type { AppConfig, FieldVar, Fleet, ForecastWire, RunEvent, Source } from "../types";
import { EVENT_TYPES } from "../types";
import { rawSSEFrames } from "./sse";
import type { Transport } from "./runReducer";

export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** The stream opened but no event arrived in time: something between us and the server buffers. */
export class BufferedStreamError extends Error {}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : res.statusText;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal, headers: { accept: "application/json" } });
  if (!res.ok) throw new HttpError(res.status, await detail(res));
  return (await res.json()) as T;
}

const q = (mode?: Source) => (mode ? `mode=${encodeURIComponent(mode)}` : "");

export const api = {
  config: (signal?: AbortSignal) => getJSON<AppConfig>("/api/config", signal),
  fleet: (mode?: Source, signal?: AbortSignal) => getJSON<Fleet>(`/api/fleet?${q(mode)}`, signal),
  forecast: (v: FieldVar, mode?: Source, signal?: AbortSignal) =>
    getJSON<ForecastWire>(`/api/forecast?var=${v}&${q(mode)}`, signal),
};

export function parseEvent(data: string): RunEvent | null {
  try {
    const e = JSON.parse(data);
    return e && EVENT_TYPES.has(e.type) ? (e as RunEvent) : null;
  } catch {
    return null;
  }
}

export interface RunRequest {
  shipmentId: string;
  mode?: Source;
  golden?: boolean;
  speed?: number;
  signal: AbortSignal;
  onOpen: (transport: Transport) => void;
  onEvent: (e: RunEvent) => void;
  /** No event within this long after the headers means a buffering proxy (fall back to stream=0). */
  firstEventTimeoutMs?: number;
}

/** Streams one run (live agent or recorded) and resolves when the server closes the stream. */
export async function streamRun(req: RunRequest): Promise<void> {
  const { shipmentId, mode, golden, speed = 1, signal, onOpen, onEvent, firstEventTimeoutMs = 8000 } = req;
  // One controller for the fetch, so the watchdog can abort it (and the server cancels the run).
  const ctrl = new AbortController();
  const forward = () => ctrl.abort();
  signal.addEventListener("abort", forward);
  let sawEvent = false;
  let buffered = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const res = golden
      ? await fetch(`/api/golden/${encodeURIComponent(shipmentId)}?speed=${speed}`, { signal: ctrl.signal })
      : await fetch("/api/runs", {
          method: "POST",
          signal: ctrl.signal,
          headers: { "content-type": "application/json", accept: "text/event-stream" },
          body: JSON.stringify({ shipment_id: shipmentId, mode }),
        });
    if (!res.ok || !res.body) throw new HttpError(res.status, await detail(res));
    onOpen(golden || res.headers.get("x-run-source") === "golden" ? "golden" : "live");
    timer = setTimeout(() => {
      if (!sawEvent) {
        buffered = true;
        ctrl.abort();
      }
    }, firstEventTimeoutMs);
    for await (const frame of rawSSEFrames(res.body)) {
      const e = parseEvent(frame.data);
      if (!e) continue;
      sawEvent = true;
      onEvent(e);
    }
  } catch (err) {
    if (buffered) throw new BufferedStreamError("the stream opened but no events arrived");
    throw err;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", forward);
  }
}

/** The same run as one JSON list (?stream=0), for networks that buffer event streams. */
export async function listRun(shipmentId: string, mode: Source | undefined, signal: AbortSignal): Promise<RunEvent[]> {
  const res = await fetch("/api/runs?stream=0", {
    method: "POST",
    signal,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ shipment_id: shipmentId, mode }),
  });
  if (!res.ok) throw new HttpError(res.status, await detail(res));
  return ((await res.json()) as unknown[]).map((e) => parseEvent(JSON.stringify(e))).filter((e): e is RunEvent => e !== null);
}

const MAX_GAP_MS = 8000;

/** Replays a list of events at their recorded pacing (t_ms), like the server's golden replay. */
export async function playEvents(
  events: RunEvent[],
  { speed = 1, signal, onEvent }: { speed?: number; signal: AbortSignal; onEvent: (e: RunEvent) => void },
): Promise<void> {
  let prev = 0;
  for (const e of events) {
    if (signal.aborted) return;
    const gap = speed > 0 ? Math.min((e.t_ms - prev) / speed, MAX_GAP_MS) : 0;
    prev = e.t_ms;
    if (gap > 0) {
      await new Promise<void>((resolve) => {
        const t = setTimeout(resolve, gap);
        signal.addEventListener("abort", () => (clearTimeout(t), resolve()), { once: true });
      });
    }
    if (signal.aborted) return;
    onEvent(e);
  }
}
