const cache = new Map<string, Intl.DateTimeFormat>();

function fmt(tz: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts);
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", ...opts });
    cache.set(key, f);
  }
  return f;
}

function parts(epochS: number, tz: string, opts: Intl.DateTimeFormatOptions) {
  const out: Record<string, string> = {};
  for (const p of fmt(tz, opts).formatToParts(new Date(epochS * 1000))) out[p.type] = p.value;
  return out;
}

/** "11:47" in the lane's time zone. */
export function clock(epochS: number, tz: string): string {
  const p = parts(epochS, tz, { hour: "2-digit", minute: "2-digit" });
  return `${p.hour}:${p.minute}`;
}

/** "MDT" */
export function zoneAbbr(epochS: number, tz: string): string {
  return parts(epochS, tz, { timeZoneName: "short" }).timeZoneName ?? "";
}

/** "Thu 11:47 MDT" (matches the server's FactSheet style). */
export function dayClock(epochS: number, tz: string): string {
  const p = parts(epochS, tz, { weekday: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
  return `${p.weekday} ${p.hour}:${p.minute} ${p.timeZoneName}`;
}

export function weekday(epochS: number, tz: string): string {
  return parts(epochS, tz, { weekday: "short" }).weekday ?? "";
}

/** "3.1 s", "420 ms", "1 m 05 s" */
export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} m ${String(s % 60).padStart(2, "0")} s`;
}

/** "+3 h 40 m" relative to a reference, for "breach in …" copy. */
export function relHours(deltaS: number): string {
  const sign = deltaS < 0 ? "−" : "+";
  const m = Math.round(Math.abs(deltaS) / 60);
  const h = Math.floor(m / 60);
  return h ? `${sign}${h} h ${String(m % 60).padStart(2, "0")} m` : `${sign}${m} m`;
}

export function epoch(iso: string | undefined, fallback: number): number {
  if (!iso) return fallback;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t / 1000 : fallback;
}

export function thousands(n: number): string {
  return n.toLocaleString("en-US");
}
