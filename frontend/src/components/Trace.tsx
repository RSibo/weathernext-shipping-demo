import clsx from "clsx";
import {
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CloudSunRain,
  Hourglass,
  LoaderCircle,
  Play,
  Route,
  ShieldAlert,
  ShieldCheck,
  Split,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { isActive, type RunState } from "../lib/runReducer";
import { buildTrace, toolTitle, type ToolStep, type TraceItem } from "../lib/trace";
import { duration, thousands } from "../lib/time";
import { useNow } from "../hooks/useNow";
import { useStore } from "../store";
import type { CandidateView, DelayOptionView, Guardrail, SegmentView, Verdict } from "../types";
import { STATUS_META } from "./ui";

const TOOL_ICON: Record<string, LucideIcon> = {
  check_route_weather: CloudSunRain,
  compute_detour_route: Route,
  evaluate_departure_delay: Hourglass,
  submit_advisory: ShieldCheck,
};

const VERDICT_STYLE: Record<Verdict | "beyond_forecast", { label: string; cls: string }> = {
  clean: { label: "Safe", cls: "text-clear-ink" },
  at_risk: { label: "Near limit", cls: "text-monitor-ink" },
  hazardous: { label: "Hazardous", cls: "text-hold-ink" },
  unmonitored: { label: "No forecast", cls: "text-ink-4" },
  beyond_forecast: { label: "Beyond forecast", cls: "text-ink-4" },
};

export function VerdictTag({ verdict, className }: { verdict: Verdict | "beyond_forecast"; className?: string }) {
  const v = VERDICT_STYLE[verdict];
  return <span className={clsx("text-2xs font-semibold tracking-wide uppercase", v.cls, className)}>{v.label}</span>;
}

/** Rail node + content, the unit of the vertical timeline. */
function Node({ icon: Icon, tone = "neutral", busy, children, last }: { icon: LucideIcon | (() => ReactNode); tone?: "neutral" | "accent" | "good" | "bad" | "warn"; busy?: boolean; children: ReactNode; last?: boolean }) {
  return (
    <li className="relative flex animate-rise gap-3 pb-4">
      {!last && <span className="absolute top-8 bottom-0 left-[0.9375rem] w-px bg-line-strong" aria-hidden />}
      <span
        className={clsx(
          "relative z-10 flex size-[1.875rem] shrink-0 items-center justify-center rounded-full border",
          tone === "accent" && "border-accent/50 bg-accent/15 text-accent-ink",
          tone === "good" && "border-clear/50 bg-clear/15 text-clear-ink",
          tone === "bad" && "border-hold/50 bg-hold/15 text-hold-ink",
          tone === "warn" && "border-monitor/50 bg-monitor/15 text-monitor-ink",
          tone === "neutral" && "border-line-strong bg-surface-2 text-ink-3",
        )}
      >
        {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Icon className="size-4" aria-hidden />}
      </span>
      <div className="min-w-0 flex-1 pt-1">{children}</div>
    </li>
  );
}

function GeminiIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path d="M12 2c.6 5.2 4.8 9.4 10 10-5.2.6-9.4 4.8-10 10-.6-5.2-4.8-9.4-10-10 5.2-.6 9.4-4.8 10-10z" fill="currentColor" />
    </svg>
  );
}

function Elapsed({ from, now }: { from: number; now: number }) {
  return <span className="tabular">{duration(Math.max(0, now - from))}</span>;
}

function Segments({ segments }: { segments: SegmentView[] }) {
  if (!segments.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {segments.slice(0, 4).map((s, i) => (
        <li key={i} className="flex items-baseline gap-2 text-xs">
          <span className={clsx("size-1.5 shrink-0 translate-y-[-1px] rounded-full", s.kind === "breach" ? "bg-hold" : "bg-monitor")} />
          <span className="tabular font-medium text-ink-1">{s.window}</span>
          <span className="truncate text-ink-3">near {s.place}</span>
          <span className="ml-auto shrink-0 font-semibold text-hold-ink tabular">{s.peak_wind}</span>
        </li>
      ))}
      {segments.length > 4 && <li className="pl-3.5 text-2xs text-ink-4">+{segments.length - 4} more</li>}
    </ul>
  );
}

function Candidates({ candidates, chosen }: { candidates: CandidateView[]; chosen?: string }) {
  return (
    <ul className="mt-2 divide-y divide-line overflow-hidden rounded-control border border-line">
      {candidates.map((c) => (
        <li key={c.option_id} className={clsx("flex items-center gap-2 px-2.5 py-1.5 text-xs", c.option_id === chosen && "bg-surface-3/60")}>
          <span className={clsx("truncate font-medium", c.option_id === chosen ? "text-ink-1" : "text-ink-2")}>{c.label}</span>
          <span className="tabular shrink-0 text-ink-3">{c.delta_duration}</span>
          <VerdictTag verdict={c.assessment.verdict} className="ml-auto shrink-0" />
        </li>
      ))}
    </ul>
  );
}

function DelayChips({ options, best }: { options: DelayOptionView[]; best?: string }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {options.map((o) => (
        <span
          key={o.option_id}
          title={`${o.depart} → ${o.arrive}${o.peak_wind ? ` · peak ${o.peak_wind}` : ""}${o.meets_deadline === false ? " · misses deadline" : ""}`}
          className={clsx("inline-flex h-6 items-center gap-1.5 rounded-chip border px-2 text-2xs", o.option_id === best ? "border-line-control bg-surface-3" : "border-line bg-surface-1")}
        >
          <span className="tabular font-semibold text-ink-1">{o.shift}</span>
          <span className={VERDICT_STYLE[o.verdict].cls}>{VERDICT_STYLE[o.verdict].label.toLowerCase()}</span>
          {o.meets_deadline === false && <span className="text-ink-4">· late</span>}
        </span>
      ))}
    </div>
  );
}

function GuardrailRow({ g }: { g: Guardrail }) {
  const ok = g.kind === "advisory_accepted";
  const Icon = ok ? ShieldCheck : ShieldAlert;
  return (
    <div className={clsx("mt-2 flex items-start gap-2 rounded-control border px-2.5 py-1.5 text-xs", ok ? "border-clear/35 bg-clear/8 text-clear-ink" : "border-monitor/35 bg-monitor/8 text-monitor-ink")}>
      <Icon className="mt-px size-3.5 shrink-0" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium">{g.message}</p>
        {g.issues.length > 0 && (
          <ul className="mt-0.5 list-disc pl-4 text-ink-3">
            {g.issues.slice(0, 3).map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StepCard({ step, now, startedAt }: { step: ToolStep; now: number; startedAt?: number }) {
  const [open, setOpen] = useState(false);
  const Icon = TOOL_ICON[step.call.name] ?? CircleCheck;
  const pending = !step.result;
  const failed = step.result && !step.result.ok;
  return (
    <div className={clsx("rounded-card border bg-surface-1/60 p-2.5", pending ? "border-accent/40" : failed ? "border-hold/40" : "border-line")}>
      <div className="flex items-center gap-2">
        <Icon className={clsx("size-4 shrink-0", pending ? "text-accent-ink" : "text-ink-3")} aria-hidden />
        <span className="truncate text-sm font-medium text-ink-1">{toolTitle(step.call.name)}</span>
        {step.parallel && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-accent/12 px-1.5 py-0.5 text-[0.625rem] font-semibold tracking-wide text-accent-ink uppercase" title="Gemini requested these tools in the same turn; they ran concurrently">
            <Split className="size-3" aria-hidden /> parallel
          </span>
        )}
        <span className="ml-auto shrink-0 text-xs text-ink-4">
          {pending ? startedAt !== undefined ? <Elapsed from={startedAt} now={now} /> : <LoaderCircle className="size-3.5 animate-spin" /> : duration(step.result!.latency_ms)}
        </span>
      </div>
      {step.result ? (
        <p className={clsx("mt-1 text-xs leading-relaxed", failed ? "text-hold-ink" : "text-ink-2")}>{step.result.error ?? step.result.summary}</p>
      ) : (
        <p className="shimmer-text mt-1 text-xs">Running…</p>
      )}
      {step.weather && (
        <>
          <p className="mt-1 text-2xs text-ink-4">{step.weather.method}</p>
          <Segments segments={step.weather.assessment.segments} />
        </>
      )}
      {step.detour && <Candidates candidates={step.detour.candidates} chosen={step.detour.chosen_id} />}
      {step.delay && <DelayChips options={step.delay.options} best={step.delay.earliest_safe_id} />}
      {step.guardrails.map((g) => (
        <GuardrailRow key={g.seq} g={g} />
      ))}
      <button type="button" onClick={() => setOpen((o) => !o)} className="mt-2 inline-flex items-center gap-1 text-2xs text-ink-4 hover:text-ink-2" aria-expanded={open}>
        <ChevronRight className={clsx("size-3 transition-transform", open && "rotate-90")} aria-hidden />
        <span className="font-mono">{step.call.name}</span>
      </button>
      {open && (
        <pre className="scroll-thin mt-1 max-h-48 overflow-auto rounded-control bg-surface-0 p-2 font-mono text-[0.6875rem] leading-relaxed text-ink-3">
          {JSON.stringify(step.call.args, null, 2)}
        </pre>
      )}
    </div>
  );
}

function Item({ item, run, now, last }: { item: TraceItem; run: RunState; now: number; last: boolean }) {
  const setTab = useStore((s) => s.setTab);
  const base = run.at.started ?? 0;
  switch (item.kind) {
    case "start": {
      const e = item.event;
      return (
        <Node icon={Play} last={last}>
          <p className="text-sm font-medium text-ink-1">
            Run started <span className="font-normal text-ink-4">· {e.shipment.shipment_id}</span>
          </p>
          <p className="mt-0.5 text-xs text-ink-3">{e.provenance.label}</p>
          {e.note && <p className="mt-0.5 text-xs text-ink-4">{e.note}</p>}
        </Node>
      );
    }
    case "turn": {
      const done = Boolean(item.end);
      return (
        <Node icon={GeminiIcon} tone="accent" busy={!done && isActive(run)} last={last}>
          <div className="flex items-baseline gap-2">
            <p className="text-sm font-medium text-ink-1">Gemini · turn {item.turn}</p>
            <span className="ml-auto text-xs text-ink-4">
              {done ? duration(item.end!.latency_ms ?? 0) : isActive(run) ? <Elapsed from={base + item.start.t_ms} now={now} /> : null}
            </span>
          </div>
          {!done && isActive(run) && <p className="shimmer-text mt-0.5 text-xs">Reading the evidence and choosing the next step…</p>}
          {item.end?.thought && <p className="mt-1 text-xs text-ink-3 italic">{item.end.thought}</p>}
          {done && item.tools.length > 0 && (
            <p className="mt-0.5 text-xs text-ink-4">
              {item.tools.length > 1 ? `Chose ${item.tools.length} tools at once` : "Chose one tool"}
              {item.end?.tokens ? ` · ${thousands(item.end.tokens.prompt)} in / ${thousands(item.end.tokens.output)} out tokens` : ""}
            </p>
          )}
          {item.tools.length > 0 && (
            <div className="mt-2 space-y-2">
              {item.tools.map((t) => (
                <StepCard key={t.call.call_id} step={t} now={now} startedAt={base + t.call.t_ms} />
              ))}
            </div>
          )}
        </Node>
      );
    }
    case "system":
      return (
        <Node icon={CloudSunRain} last={last}>
          <div className="space-y-2">
            {item.tools.map((t) => (
              <StepCard key={t.call.call_id} step={t} now={now} startedAt={base + t.call.t_ms} />
            ))}
          </div>
        </Node>
      );
    case "guardrail":
      return (
        <Node icon={item.event.kind === "advisory_accepted" ? ShieldCheck : ShieldAlert} tone={item.event.kind === "advisory_accepted" ? "good" : "warn"} last={last}>
          <p className="text-sm font-medium text-ink-1">{item.event.kind === "fallback_used" ? "Template advisory used" : "Guardrail"}</p>
          <GuardrailRow g={item.event} />
        </Node>
      );
    case "advisory": {
      const m = STATUS_META[item.event.status];
      return (
        <Node icon={m.icon} tone={item.event.status === "CLEAR" ? "good" : item.event.status === "MONITOR" ? "warn" : "bad"} last={last}>
          <p className={clsx("text-xs font-semibold tracking-wide uppercase", m.ink)}>Advisory · {m.label}</p>
          <p className="mt-0.5 text-sm font-medium text-ink-1">{item.event.headline}</p>
          <button type="button" className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent-ink hover:underline" onClick={() => setTab("advisory")}>
            Open advisory <ChevronRight className="size-3.5" aria-hidden />
          </button>
        </Node>
      );
    }
    case "done": {
      const e = item.event;
      const tokens = e.tokens.prompt + e.tokens.output + e.tokens.thoughts;
      return (
        <Node icon={CircleCheck} tone="good" last>
          <p className="text-sm font-medium text-ink-1" role="status">
            Done in {duration(e.total_ms)}
          </p>
          <p className="mt-0.5 text-xs text-ink-3">
            {e.turns} Gemini turns · {thousands(tokens)} tokens · {e.routes_live + e.routes_cached} Routes API calls
            {e.routes_cached ? ` (${e.routes_cached} cached)` : ""}
          </p>
        </Node>
      );
    }
    case "error":
      return (
        <Node icon={CircleAlert} tone="bad" last>
          <p className="text-sm font-medium text-ink-1">Run stopped</p>
          <p className="mt-0.5 text-xs text-ink-3">{item.event.message}</p>
        </Node>
      );
  }
}

export function Trace({ run }: { run: RunState }) {
  const items = useMemo(() => buildTrace(run.events), [run.events]);
  const active = isActive(run);
  const now = useNow(active);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [items.length, run.events.length]);
  useEffect(() => {
    stick.current = true;
  }, [run.runId]);

  return (
    <div
      ref={scroller}
      className="scroll-thin h-full overflow-y-auto px-4 pt-4"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
    >
      <ol aria-live="polite" aria-label="Agent trace">
        {items.map((it, i) => (
          <Item key={it.key} item={it} run={run} now={now} last={i === items.length - 1 && !active} />
        ))}
        {active && items.length === 0 && (
          <Node icon={Play} busy last>
            <p className="shimmer-text text-sm">Connecting to the agent…</p>
          </Node>
        )}
      </ol>
    </div>
  );
}
