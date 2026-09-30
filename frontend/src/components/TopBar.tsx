import clsx from "clsx";
import { FlaskConical, History, Keyboard, Radio } from "lucide-react";
import { useStore } from "../store";
import type { Source } from "../types";
import { Kbd, Segmented } from "./ui";

const SOURCE_META: Record<Source, { label: string; icon: typeof Radio; dot: string }> = {
  live: { label: "Live", icon: Radio, dot: "bg-clear-ink" },
  replay: { label: "Replay", icon: History, dot: "bg-accent-ink" },
  scenario: { label: "Scenario", icon: FlaskConical, dot: "bg-monitor-ink" },
};

function Wordmark() {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="9" fill="#1a1a19" stroke="rgba(255,255,255,.12)" />
        <path d="M6 20.5c4 0 5-8.5 10-8.5s6 8.5 10 8.5" fill="none" stroke="#6da7ec" strokeWidth="2.4" strokeLinecap="round" />
        <path d="M6 24.5h20" stroke="#2a78d6" strokeWidth="1.6" strokeLinecap="round" opacity=".7" />
        <circle cx="16" cy="12" r="2.6" fill="#fff" />
      </svg>
      <div className="min-w-0 leading-tight">
        <div className="truncate text-[0.9375rem] font-semibold tracking-tight text-ink-1">
          WeatherNext 3 <span className="font-normal text-ink-4">·</span> Route Disruption Agent
        </div>
        <div className="hidden truncate text-2xs text-ink-4 sm:block">Gemini reads the forecast along every truck's route and decides</div>
      </div>
    </div>
  );
}

/** Where the forecast on screen comes from. Always visible: the honesty contract of the demo. */
export function ProvenancePill() {
  const fleet = useStore((s) => s.fleet);
  if (!fleet) return null;
  const p = fleet.provenance;
  const meta = SOURCE_META[p.source];
  const Icon = meta.icon;
  const detail = p.label.includes("·") ? p.label.split("·").slice(1).join("·").trim() : p.label;
  return (
    <div
      className="flex h-8 min-w-0 items-center gap-2 rounded-full border border-line-strong bg-surface-1 pr-3 pl-1 text-xs"
      title={p.dataset ? `${p.label}\nBigQuery: ${p.dataset}` : p.label}
      data-testid="provenance"
    >
      <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-surface-3 px-2 font-semibold tracking-wide text-ink-1 uppercase">
        <span className={clsx("size-1.5 rounded-full", meta.dot, p.source === "live" && "animate-breathe")} />
        <Icon className="size-3.5" aria-hidden />
        {meta.label}
      </span>
      <span className="truncate text-ink-3">{detail}</span>
    </div>
  );
}

export function TopBar() {
  const config = useStore((s) => s.config);
  const mode = useStore((s) => s.mode);
  const setHelp = useStore((s) => s.setHelp);
  const modes = config?.modes ?? [];
  return (
    <header className="relative z-30 flex h-14 shrink-0 items-center gap-4 border-b border-line bg-surface-0 px-4 lg:px-5">
      <Wordmark />
      <div className="hidden min-w-0 flex-1 items-center justify-center gap-3 md:flex">
        <ProvenancePill />
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {modes.length > 1 && mode && (
          <Segmented
            label="Forecast source"
            value={mode}
            options={modes.map((m) => ({ value: m.id, label: SOURCE_META[m.id].label, title: m.label }))}
            onChange={(v) => {
              const url = new URL(window.location.href);
              url.searchParams.set("mode", v);
              window.location.assign(url.toString());
            }}
          />
        )}
        {config && (
          <span className="hidden h-8 items-center gap-2 rounded-full border border-line px-3 text-xs text-ink-3 xl:inline-flex" title="Model driving the agent">
            <GeminiMark />
            <span className="font-mono text-[0.72rem] text-ink-2">{config.model}</span>
          </span>
        )}
        <button type="button" className="btn btn-quiet hidden h-8 px-2 lg:inline-flex" onClick={() => setHelp(true)} aria-label="Keyboard shortcuts">
          <Keyboard className="size-4" aria-hidden />
          <Kbd>?</Kbd>
        </button>
      </div>
    </header>
  );
}

function GeminiMark() {
  return (
    <svg viewBox="0 0 24 24" className="size-3.5" aria-hidden>
      <defs>
        <linearGradient id="gm" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6ba6f5" />
          <stop offset="1" stopColor="#cde2fb" />
        </linearGradient>
      </defs>
      <path d="M12 2c.6 5.2 4.8 9.4 10 10-5.2.6-9.4 4.8-10 10-.6-5.2-4.8-9.4-10-10 5.2-.6 9.4-4.8 10-10z" fill="url(#gm)" />
    </svg>
  );
}
