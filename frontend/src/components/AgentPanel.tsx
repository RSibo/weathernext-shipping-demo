import clsx from "clsx";
import type { ReactNode } from "react";
import { CircleAlert, History, RotateCcw, Sparkles, Square, TriangleAlert } from "lucide-react";
import { worstSegment } from "../lib/geo";
import { isActive, type RunState } from "../lib/runReducer";
import type { FleetEntry } from "../types";
import { duration } from "../lib/time";
import { useNow } from "../hooks/useNow";
import { selectEntry, selectRun, useStore, type Tab } from "../store";
import { AdvisoryView } from "./AdvisoryView";
import { DetailsView } from "./DetailsView";
import { Trace } from "./Trace";
import { HINT_META, Kbd, Segmented, SectionLabel, STATUS_META, StatusChip } from "./ui";

function RunClock({ run }: { run: RunState }) {
  const active = isActive(run);
  const now = useNow(active, 100);
  if (run.completed) return <span className="tabular">{duration(run.completed.total_ms)}</span>;
  if (!active || run.at.started === undefined) return null;
  return <span className="tabular">{duration(now - run.at.started)}</span>;
}

function Failure({ run, onGolden, onRetry }: { run: RunState; onGolden: () => void; onRetry: () => void }) {
  const f = run.failure!;
  const capped = f.code === "capped";
  return (
    <div className="mx-4 mt-3 rounded-card border border-hold/35 bg-hold/8 p-3">
      <div className="flex items-start gap-2">
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-hold-ink" aria-hidden />
        <div className="min-w-0 text-sm">
          <p className="font-medium text-ink-1">{capped ? "Live runs are used up for today" : "The live run didn't finish"}</p>
          <p className="mt-0.5 text-xs text-ink-3">{f.message}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {f.fallback === "golden" && (
              <button type="button" className="btn btn-primary h-8 text-xs" onClick={onGolden}>
                <History className="size-3.5" aria-hidden /> Play the recorded run
              </button>
            )}
            {!capped && (
              <button type="button" className="btn btn-ghost h-8 text-xs" onClick={onRetry}>
                <RotateCcw className="size-3.5" aria-hidden /> Try again
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AgentPanel({ className }: { className?: string }) {
  const entry = useStore(selectEntry);
  const run = useStore(selectRun);
  const tab = useStore((s) => s.tab);
  const stage = useStore((s) => s.stage);
  const config = useStore((s) => s.config);
  const setTab = useStore((s) => s.setTab);
  const runAgent = useStore((s) => s.runAgent);
  const stopRun = useStore((s) => s.stopRun);
  if (!entry) return null;
  const sh = entry.shipment;
  const active = isActive(run);
  const hasGolden = config?.golden.includes(sh.shipment_id) ?? false;
  const [origin, dest] = sh.lane.split("→").map((x) => x.trim());
  const tabs: { value: Tab; label: ReactNode }[] = [
    { value: "trace", label: "Trace" },
    {
      value: "advisory",
      label: (
        <>
          Advisory
          {run.advisory && <span className={clsx("size-1.5 rounded-full", STATUS_META[run.advisory.status].color)} aria-hidden />}
        </>
      ),
    },
  ];
  if (!stage) tabs.push({ value: "details", label: "Details" });
  const shownTab = stage && tab === "details" ? "trace" : tab;

  return (
    <aside className={clsx("glass flex min-h-0 flex-col overflow-hidden rounded-panel", className)} aria-label="Agent">
      <div className="border-b border-line px-4 pt-4 pb-3">
        <div className="flex items-center gap-2 text-xs text-ink-4">
          <span className="font-mono">{sh.shipment_id}</span>
          <span>·</span>
          <span className="truncate">{sh.carrier ?? sh.cargo}</span>
        </div>
        <h2 className="mt-1 text-lg leading-snug font-semibold tracking-tight text-ink-1">
          {origin} <span className="font-normal text-ink-4">→</span> {dest}
        </h2>
        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
          <div className="flex gap-1">
            <dt className="text-ink-4">Cargo</dt>
            <dd className="text-ink-2">{sh.cargo}</dd>
          </div>
          <div className="flex gap-1" title={sh.threshold_basis}>
            <dt className="text-ink-4">Wind limit</dt>
            <dd className="font-semibold text-ink-1">{sh.wind_threshold}</dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-ink-4">Departs</dt>
            <dd className="text-ink-2">{sh.depart}</dd>
          </div>
          {sh.deadline && (
            <div className="flex gap-1">
              <dt className="text-ink-4">Due</dt>
              <dd className="text-ink-2">{sh.deadline}</dd>
            </div>
          )}
        </dl>
        <div className="mt-3 flex items-center gap-2">
          {active ? (
            <button type="button" className="btn btn-ghost" onClick={stopRun}>
              <Square className="size-3.5 fill-current" aria-hidden /> Stop
            </button>
          ) : (
            <button type="button" className="btn btn-primary" onClick={() => runAgent()} data-testid="run-agent">
              <Sparkles className="size-4" aria-hidden />
              {run.phase === "idle" ? "Run agent" : "Run again"}
              <Kbd className="!border-white/25 !bg-white/15 !text-white">R</Kbd>
            </button>
          )}
          {hasGolden && !active && (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => runAgent(undefined, { golden: true })}
              title="Replay a recorded Gemini run for this truck at its real pace"
              aria-label="Play the recorded run"
            >
              <History className="size-4" aria-hidden />
              {run.phase === "idle" && "Recorded"}
              <Kbd>G</Kbd>
            </button>
          )}
          <div className="ml-auto flex items-center gap-2 text-xs whitespace-nowrap text-ink-3">
            {run.transport === "golden" && run.phase !== "idle" && <span className="rounded-full border border-line-strong px-2 py-0.5 text-2xs font-semibold tracking-wide text-ink-2 uppercase">Recorded run</span>}
            {run.transport === "list" && <span className="rounded-full border border-line-strong px-2 py-0.5 text-2xs text-ink-2" title="This network buffers streams, so the run arrived as one response and is replayed at its real pace">Buffered</span>}
            <RunClock run={run} />
          </div>
        </div>
      </div>
      {active && <div className="h-0.5 w-full animate-shimmer bg-[linear-gradient(90deg,transparent,var(--color-accent),transparent)] bg-[length:200%_100%]" aria-hidden />}
      <div className="flex items-center gap-2 px-4 pt-3">
        <Segmented label="Agent views" value={shownTab} onChange={setTab} options={tabs} size="md" />
      </div>
      {run.phase === "error" && run.failure && <Failure run={run} onGolden={() => runAgent(undefined, { golden: true })} onRetry={() => runAgent()} />}
      <div className="min-h-0 flex-1">
        {shownTab === "trace" &&
          (run.phase === "idle" ? (
            <Idle entry={entry} />
          ) : (
            <Trace run={run} />
          ))}
        {shownTab === "advisory" && <AdvisoryView run={run} />}
        {shownTab === "details" && <DetailsView run={run} />}
      </div>
    </aside>
  );
}

function Idle({ entry }: { entry: FleetEntry }) {
  const a = entry.assessment;
  const breaches = a?.segments ?? [];
  const worst = worstSegment(breaches);
  const meta = HINT_META[entry.hint];
  return (
    <div className="scroll-thin flex h-full flex-col gap-5 overflow-y-auto px-4 pt-4 pb-6 text-sm text-ink-3">
      <section className="rounded-card border border-line bg-surface-1/50 p-3" data-testid="scan-context">
        <div className="flex items-center justify-between gap-2">
          <SectionLabel>Fleet scan</SectionLabel>
          <StatusChip meta={meta} />
        </div>
        {worst ? (
          <p className="mt-2 flex items-start gap-1.5 text-sm text-ink-1">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-hold-ink" aria-hidden />
            {breaches.length > 1 ? (
              <span>
                Over the {entry.shipment.wind_threshold} limit on {breaches.length} stretches.{" "}
                <span className="text-ink-3">
                  Worst {worst.peak_wind}, {worst.window} near {worst.place}.
                </span>
              </span>
            ) : (
              <span>
                Over the {entry.shipment.wind_threshold} limit {worst.window} near {worst.place}
                <span className="text-ink-3"> · peak {worst.peak_wind}</span>
              </span>
            )}
          </p>
        ) : a?.peak_wind ? (
          <p className="mt-2 text-sm text-ink-1">
            Peak {a.peak_wind} against a {entry.shipment.wind_threshold} limit
          </p>
        ) : (
          <p className="mt-2 text-sm text-ink-3">{entry.error ?? "No forecast along this lane."}</p>
        )}
        <p className="mt-1.5 text-xs text-ink-4">A deterministic check, no AI. The agent decides what to do.</p>
      </section>
      <div>
        <p className="font-medium text-ink-1">What the agent does</p>
        <ol className="mt-3 space-y-3">
          {[
            ["Reads WeatherNext 3", "Samples the p90 wind forecast every 5 km along the route, at the hour the truck will be there."],
            ["Weighs the options", "If the cargo's limit is crossed, searches for safer routes and later departures in parallel."],
            ["Decides, with receipts", "Clear, monitor, reroute or hold. Every number in the advisory is checked against the tools' results."],
          ].map(([t, d], i) => (
            <li key={t} className="flex gap-3">
              <span className="tabular flex size-6 shrink-0 items-center justify-center rounded-full border border-line-strong text-xs font-semibold text-ink-2">{i + 1}</span>
              <div>
                <p className="font-medium text-ink-1">{t}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{d}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <p className="text-xs text-ink-4">
        Press <Kbd>R</Kbd> to run it live on Gemini, or <Kbd>G</Kbd> for a recorded run.
      </p>
    </div>
  );
}
