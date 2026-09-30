import clsx from "clsx";
import { useMemo } from "react";
import type { RunState } from "../lib/runReducer";
import { toolTitle } from "../lib/trace";
import { duration, thousands } from "../lib/time";
import type { RunEvent } from "../types";
import { SectionLabel } from "./ui";

interface Bar {
  key: string;
  label: string;
  start: number;
  end: number;
  kind: "llm" | "tool";
}

/** Short names for the waterfall's narrow label column. */
const SHORT: Record<string, string> = {
  check_route_weather: "Forecast check",
  compute_detour_route: "Route search",
  evaluate_departure_delay: "Departure search",
  submit_advisory: "Advisory check",
};

function bars(events: RunEvent[]): Bar[] {
  const out: Bar[] = [];
  const turnStart = new Map<number, number>();
  const callStart = new Map<string, { t: number; name: string }>();
  for (const e of events) {
    if (e.type === "model_turn") {
      if (e.phase === "start") turnStart.set(e.turn, e.t_ms);
      else out.push({ key: `turn-${e.turn}`, label: `Gemini turn ${e.turn}`, start: turnStart.get(e.turn) ?? e.t_ms - (e.latency_ms ?? 0), end: e.t_ms, kind: "llm" });
    } else if (e.type === "tool_call") {
      callStart.set(e.call_id, { t: e.t_ms, name: e.name });
    } else if (e.type === "tool_result") {
      const c = callStart.get(e.call_id);
      const end = (c?.t ?? e.t_ms) + e.latency_ms;
      out.push({ key: e.call_id, label: SHORT[e.name] ?? toolTitle(e.name), start: c?.t ?? e.t_ms - e.latency_ms, end: Math.max(end, (c?.t ?? 0) + 1), kind: "tool" });
    }
  }
  return out;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-card border border-line bg-surface-2/60 px-3 py-2">
      <div className="text-2xs font-medium tracking-wide text-ink-4 uppercase">{label}</div>
      <div className="tabular mt-0.5 text-base font-semibold text-ink-1">{value}</div>
      {sub && <div className="truncate text-2xs text-ink-4">{sub}</div>}
    </div>
  );
}

export function DetailsView({ run }: { run: RunState }) {
  const rows = useMemo(() => bars(run.events), [run.events]);
  const done = run.completed;
  const total = done?.total_ms ?? Math.max(1, ...rows.map((r) => r.end));
  if (!run.events.length) {
    return <div className="flex h-full items-center justify-center p-8 text-center text-sm text-ink-4">Timings, tokens and data sources appear here after a run.</div>;
  }
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * total);
  return (
    <div className="scroll-thin h-full space-y-5 overflow-y-auto p-4">
      <div>
        <SectionLabel className="mb-2">Where the time went</SectionLabel>
        <div className="relative">
          <div className="pointer-events-none absolute inset-y-0 right-0 left-[calc(34%+0.5rem)]">
            {ticks.map((t) => (
              <span key={t} className="absolute inset-y-0 w-px bg-line" style={{ left: `${(t / total) * 100}%` }} />
            ))}
          </div>
          <ul className="relative space-y-1.5">
            {rows.map((r) => (
              <li key={r.key} className="flex items-center gap-2 text-xs">
                <span className="w-[34%] truncate text-ink-3" title={r.label}>
                  {r.label}
                </span>
                <span className="relative h-4 flex-1">
                  <span
                    className={clsx("absolute inset-y-0.5 rounded-[3px]", r.kind === "llm" ? "bg-accent" : "bg-ink-3")}
                    style={{ left: `${(r.start / total) * 100}%`, width: `max(3px, ${((r.end - r.start) / total) * 100}%)` }}
                    title={duration(r.end - r.start)}
                  />
                </span>
              </li>
            ))}
          </ul>
          <div className="relative mt-1 ml-[calc(34%+0.5rem)] h-4 text-[0.625rem] whitespace-nowrap text-ink-4">
            {ticks.map((t, i) => (
              <span key={t} className={clsx("tabular absolute", i === 0 ? "" : i === ticks.length - 1 ? "-translate-x-full" : "-translate-x-1/2")} style={{ left: `${(t / total) * 100}%` }}>
                {duration(t)}
              </span>
            ))}
          </div>
        </div>
        <div className="mt-2 flex gap-4 text-2xs text-ink-4">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-accent" /> Gemini
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-ink-3" /> Tools (forecast, Routes API)
          </span>
        </div>
      </div>
      {done && (
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Total" value={duration(done.total_ms)} sub={`${done.turns} Gemini turns`} />
          <Stat label="Gemini" value={duration(done.llm_ms)} sub={`${Math.round((done.llm_ms / done.total_ms) * 100)}% of the run`} />
          <Stat label="Tools" value={duration(done.tools_ms)} sub="forecast sampling, routing" />
          <Stat label="Tokens" value={thousands(done.tokens.prompt + done.tokens.output)} sub={`${thousands(done.tokens.prompt)} in · ${thousands(done.tokens.output)} out`} />
          <Stat label="Routes API" value={`${done.routes_live + done.routes_cached} calls`} sub={done.routes_cached ? `${done.routes_cached} served from cache` : "all live"} />
          <Stat label="Model" value={done.model} />
        </div>
      )}
      {run.started && (
        <div className="space-y-1 text-xs text-ink-3">
          <SectionLabel className="mb-1.5">Data</SectionLabel>
          <p>{run.started.provenance.label}</p>
          {run.started.provenance.dataset && <p className="font-mono text-[0.6875rem] text-ink-4">{run.started.provenance.dataset}</p>}
          {run.weather && <p>{run.weather.method}</p>}
          <p className="text-ink-4">Car-profile routing; truck restrictions are not modelled.</p>
        </div>
      )}
    </div>
  );
}
