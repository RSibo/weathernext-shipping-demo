import clsx from "clsx";
import { Check, FileWarning, MessageSquareText, Send, ShieldCheck, Truck, X } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import type { RunState } from "../lib/runReducer";
import { useStore } from "../store";
import type { Advisory, Metric } from "../types";
import { VerdictTag } from "./Trace";
import { SectionLabel, STATUS_META } from "./ui";

const TONE_TEXT: Record<Metric["tone"], string> = {
  neutral: "text-ink-1",
  good: "text-clear-ink",
  warn: "text-monitor-ink",
  bad: "text-hold-ink",
};

const STATUS_WASH: Record<Advisory["status"], string> = {
  CLEAR: "from-clear/18",
  MONITOR: "from-monitor/16",
  REROUTE: "from-reroute/18",
  HOLD: "from-hold/18",
};

function Kpis({ metrics }: { metrics: Metric[] }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {metrics.slice(0, 4).map((m, i) => (
        <motion.div
          key={m.key}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.12 + i * 0.06, duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="rounded-card border border-line bg-surface-2/70 px-3 py-2.5"
        >
          <div className="text-2xs font-medium tracking-wide text-ink-4 uppercase">{m.label}</div>
          <div className={clsx("tabular mt-0.5 text-xl leading-tight font-semibold tracking-tight", TONE_TEXT[m.tone])}>{m.value}</div>
        </motion.div>
      ))}
    </div>
  );
}

interface OptionRow {
  id: string;
  label: string;
  detail: string;
  verdict: Parameters<typeof VerdictTag>[0]["verdict"];
  chosen: boolean;
  note?: string;
}

function optionRows(run: RunState, a: Advisory): { routes: OptionRow[]; departures: OptionRow[] } {
  const routes: OptionRow[] = [];
  const baseline = run.route;
  const wx = run.weather?.assessment;
  if (baseline && wx) {
    routes.push({
      id: "baseline",
      label: `Stay on ${baseline.description}`,
      detail: [wx.peak_wind && `peak ${wx.peak_wind}`, `arrive ${baseline.arrive}`].filter(Boolean).join(" · "),
      verdict: wx.verdict,
      chosen: a.chosen_option_id === "baseline",
    });
  }
  for (const c of run.detour?.candidates ?? []) {
    routes.push({
      id: c.option_id,
      label: c.label,
      detail: [c.delta_duration, c.delta_distance, c.assessment.peak_wind && `peak ${c.assessment.peak_wind}`].filter(Boolean).join(" · "),
      verdict: c.assessment.verdict,
      chosen: a.chosen_option_id === c.option_id,
      note: c.meets_deadline === false ? "misses deadline" : undefined,
    });
  }
  const departures: OptionRow[] = (run.delay?.options ?? []).map((o) => ({
    id: o.option_id,
    label: `Depart ${o.shift}`,
    detail: [`${o.depart} → ${o.arrive}`, o.peak_wind && `peak ${o.peak_wind}`].filter(Boolean).join(" · "),
    verdict: o.verdict,
    chosen: a.chosen_option_id === o.option_id,
    note: o.meets_deadline === false ? "misses deadline" : undefined,
  }));
  return { routes, departures };
}

function Options({ title, rows }: { title: string; rows: OptionRow[] }) {
  if (!rows.length) return null;
  return (
    <div>
      <SectionLabel className="mb-1.5">{title}</SectionLabel>
      <ul className="divide-y divide-line overflow-hidden rounded-card border border-line">
        {rows.map((r) => (
          <li key={r.id} className={clsx("flex items-center gap-3 px-3 py-2", r.chosen && "bg-surface-3/70")}>
            <span className={clsx("flex size-5 shrink-0 items-center justify-center rounded-full", r.chosen ? "bg-ink-1 text-surface-0" : "text-ink-4")}>
              {r.chosen ? <Check className="size-3.5" strokeWidth={3} aria-label="Chosen" /> : r.verdict === "hazardous" ? <X className="size-3.5" aria-hidden /> : <span className="size-1.5 rounded-full bg-current" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className={clsx("truncate text-sm", r.chosen ? "font-semibold text-ink-1" : "font-medium text-ink-2")}>{r.label}</div>
              <div className="text-xs text-pretty text-ink-3">
                {r.detail}
                {r.note && <span className="text-ink-4"> · {r.note}</span>}
              </div>
            </div>
            <VerdictTag verdict={r.verdict} className="shrink-0" />
          </li>
        ))}
      </ul>
    </div>
  );
}

function DriverMessage({ text, shipmentId }: { text: string; shipmentId: string }) {
  const showToast = useStore((s) => s.showToast);
  const [sent, setSent] = useState(false);
  return (
    <div>
      <SectionLabel className="mb-1.5">Message to driver</SectionLabel>
      <div className="rounded-card border border-line bg-surface-2/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-2xs text-ink-4">
          <span className="flex size-5 items-center justify-center rounded-full bg-surface-3 text-ink-2">
            <Truck className="size-3" aria-hidden />
          </span>
          <span>Driver · {shipmentId}</span>
          <MessageSquareText className="ml-auto size-3.5" aria-hidden />
          <span className="tabular">{text.length}/300</span>
        </div>
        <p className="rounded-[1rem] rounded-tl-sm bg-accent px-3.5 py-2.5 text-sm leading-relaxed text-white">{text}</p>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            className={clsx("btn", sent ? "btn-ghost" : "btn-primary")}
            onClick={() => {
              setSent(true);
              showToast("Approved. Demo mode: no message was sent.", "good");
            }}
            disabled={sent}
          >
            {sent ? <Check className="size-4" aria-hidden /> : <Send className="size-4" aria-hidden />}
            {sent ? "Approved" : "Approve & notify driver"}
          </button>
          <span className="text-2xs text-ink-4">Demo: no message is sent</span>
        </div>
      </div>
    </div>
  );
}

export function AdvisoryView({ run }: { run: RunState }) {
  const a = run.advisory;
  if (!a) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-ink-4">
        {run.phase === "idle" ? "Run the agent to get an advisory for this truck." : "The advisory appears here when the agent submits it."}
      </div>
    );
  }
  const m = STATUS_META[a.status];
  const Icon = m.icon;
  const { routes, departures } = optionRows(run, a);
  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className={clsx("bg-gradient-to-b to-transparent px-4 pt-4 pb-2", STATUS_WASH[a.status])}>
        <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }} className="flex items-center gap-3">
          <span className={clsx("flex size-11 items-center justify-center rounded-2xl text-surface-0", m.color)}>
            <Icon className="size-6" strokeWidth={2.4} aria-hidden />
          </span>
          <div>
            <div className={clsx("text-sm font-bold tracking-[0.12em] uppercase", m.ink)} data-testid="advisory-status">
              {m.label}
            </div>
            <div className="text-2xs text-ink-4">{a.source === "gemini" ? "Gemini advisory · validated" : "Template advisory"}</div>
          </div>
        </motion.div>
        <h2 className="mt-3 text-xl leading-snug font-semibold tracking-tight text-balance text-ink-1">{a.headline}</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-2">{a.recommendation}</p>
      </div>
      <div className="space-y-4 px-4 pt-2 pb-5">
        <Kpis metrics={a.metrics} />
        <div className={clsx("flex items-start gap-2 rounded-control border px-3 py-2 text-xs", a.source === "gemini" ? "border-clear/30 bg-clear/8 text-clear-ink" : "border-monitor/30 bg-monitor/8 text-monitor-ink")}>
          {a.source === "gemini" ? <ShieldCheck className="mt-px size-4 shrink-0" aria-hidden /> : <FileWarning className="mt-px size-4 shrink-0" aria-hidden />}
          <span>
            {a.source === "gemini" ? (
              <>
                <span className="font-semibold">Validated.</span> <span className="text-ink-2">Decision allowed by the evidence; {a.grounded_numbers} of {a.grounded_numbers} numbers traced to tool results.</span>
              </>
            ) : (
              <>
                <span className="font-semibold">Template advisory.</span> <span className="text-ink-2">Gemini's draft did not pass validation, so the facts were written from a template.</span>
              </>
            )}
          </span>
        </div>
        <Options title="Routes considered" rows={routes} />
        <Options title="Departure times considered" rows={departures} />
        <div>
          <SectionLabel className="mb-1.5">Why</SectionLabel>
          <p className="text-sm leading-relaxed text-ink-2">{a.rationale}</p>
        </div>
        <DriverMessage text={a.driver_message} shipmentId={run.started?.shipment.shipment_id ?? ""} />
      </div>
    </div>
  );
}
