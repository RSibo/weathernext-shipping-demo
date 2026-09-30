import clsx from "clsx";
import { LoaderCircle, ScanLine, TriangleAlert } from "lucide-react";
import { motion } from "motion/react";
import { worstSegment } from "../lib/geo";
import { isActive } from "../lib/runReducer";
import { duration } from "../lib/time";
import { useStore } from "../store";
import type { FleetEntry, Hint } from "../types";
import { RiskStrip } from "./RiskStrip";
import { HINT_META, Kbd, SectionLabel, STATUS_META, StatusChip } from "./ui";

function Summary({ entries }: { entries: FleetEntry[] }) {
  const count = (h: Hint) => entries.filter((e) => e.hint === h).length;
  const items: { hint: Hint; n: number; label: string }[] = [
    { hint: "ACTION", n: count("ACTION"), label: "need action" },
    { hint: "MONITOR", n: count("MONITOR"), label: "monitor" },
    { hint: "CLEAR", n: count("CLEAR"), label: "clear" },
  ];
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {items.map(({ hint, n, label }) => {
        const m = HINT_META[hint];
        const Icon = m.icon;
        return (
          <span key={hint} className={clsx("inline-flex items-center gap-1", n ? m.ink : "text-ink-4")}>
            <Icon className="size-3.5" strokeWidth={2.4} aria-hidden />
            <span className="tabular font-semibold">{n}</span>
            <span className={n ? "text-ink-3" : "text-ink-4"}>{label}</span>
          </span>
        );
      })}
    </div>
  );
}

function FleetCard({ entry, index }: { entry: FleetEntry; index: number }) {
  const id = entry.shipment.shipment_id;
  const selected = useStore((s) => s.selectedId === id);
  const run = useStore((s) => s.runs[id]);
  const scanning = useStore((s) => s.scanning);
  const select = useStore((s) => s.select);
  const hover = useStore((s) => s.hover);
  const sh = entry.shipment;
  const samples = run?.weather?.samples ?? entry.samples;
  const advisory = run?.advisory;
  // The worst stretch, the same one the map labels and the scrubber animates to.
  const breaches = entry.assessment?.segments ?? [];
  const worst = worstSegment(breaches);
  const running = isActive(run);
  const [origin, dest] = sh.lane.split("→").map((x) => x.trim());

  return (
    <motion.li
      layout="position"
      initial={false}
      animate={scanning ? { opacity: [1, 0.35, 1] } : { opacity: 1 }}
      transition={scanning ? { duration: 0.7, delay: index * 0.09 } : { duration: 0.2 }}
    >
      <button
        type="button"
        onClick={() => select(id)}
        onPointerEnter={() => hover(id)}
        onPointerLeave={() => hover(undefined)}
        aria-pressed={selected}
        data-testid={`fleet-${id}`}
        className={clsx(
          "group relative block w-full overflow-hidden rounded-card border p-3 text-left transition-[background-color,border-color,box-shadow] duration-150",
          selected ? "border-accent/55 bg-surface-2 shadow-[0_0_0_1px_rgb(50_119_203/0.3)]" : "border-line bg-surface-1/40 hover:border-line-strong hover:bg-surface-2/70",
        )}
      >
        {selected && <span className="absolute inset-y-3 left-0 w-[3px] rounded-r bg-accent" aria-hidden />}
        <div className="flex items-center gap-2">
          <Kbd className="!h-[1.125rem] !min-w-[1.125rem] !text-[0.625rem]">{index + 1}</Kbd>
          <span className="font-mono text-[0.72rem] text-ink-4">{id}</span>
          <span className="ml-auto">
            {running ? (
              <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 px-2 text-2xs font-semibold tracking-wide text-accent-ink uppercase">
                <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> Agent
              </span>
            ) : advisory ? (
              <StatusChip meta={STATUS_META[advisory.status]} />
            ) : (
              <StatusChip meta={HINT_META[entry.hint]} />
            )}
          </span>
        </div>
        <div className="mt-2 text-[0.9375rem] leading-snug font-semibold text-ink-1">
          {origin} <span className="font-normal text-ink-4">→</span> {dest}
        </div>
        <div className="mt-0.5 truncate text-xs text-ink-3">
          {sh.cargo ?? "Freight"} <span className="text-ink-4">·</span> limit <span className="font-semibold text-ink-2">{sh.wind_threshold}</span>
        </div>
        <RiskStrip samples={samples} limit={sh.wind_threshold_mph} className="mt-2.5" />
        <div className="mt-2 min-h-4 text-xs">
          {advisory ? (
            <p className="line-clamp-2 text-ink-2">{advisory.headline}</p>
          ) : worst ? (
            <p className="flex items-start gap-1.5 text-hold-ink">
              <TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
              <span>
                {breaches.length > 1 ? "Worst" : "Breach"}{" "}
                <span className="text-ink-2">
                  {worst.window} near {worst.place} · {worst.peak_wind}
                </span>
              </span>
            </p>
          ) : entry.assessment?.peak_wind ? (
            <p className="text-ink-3">
              Peak {entry.assessment.peak_wind} <span className="text-ink-4">· departs {sh.depart}</span>
            </p>
          ) : entry.error ? (
            <p className="text-ink-4">{entry.error}</p>
          ) : null}
        </div>
      </button>
    </motion.li>
  );
}

export function FleetRail({ className }: { className?: string }) {
  const fleet = useStore((s) => s.fleet);
  const scanning = useStore((s) => s.scanning);
  const rescan = useStore((s) => s.rescan);
  if (!fleet) return null;
  return (
    <aside className={clsx("glass flex min-h-0 flex-col rounded-panel", className)} aria-label="Fleet">
      <div className="border-b border-line px-4 pt-4 pb-3">
        <div className="flex items-center gap-2">
          <SectionLabel>Fleet · {fleet.entries.length} trucks</SectionLabel>
          <button type="button" className="btn btn-quiet ml-auto h-7 gap-1.5 px-2 text-xs" onClick={() => rescan()} disabled={scanning} title="Re-score every lane against the forecast (no AI involved)">
            <ScanLine className={clsx("size-3.5", scanning && "animate-pulse")} aria-hidden />
            Scan <Kbd className="!h-4 !min-w-4 !text-[0.6rem]">S</Kbd>
          </button>
        </div>
        <div className="mt-2">
          <Summary entries={fleet.entries} />
        </div>
        <p className="mt-1.5 text-2xs text-ink-4">
          Every lane scored against WeatherNext 3 at its own ETAs in <span className="tabular text-ink-3">{duration(fleet.scan_ms)}</span>
        </p>
      </div>
      <ol className="scroll-thin flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
        {fleet.entries.map((e, i) => (
          <FleetCard key={e.shipment.shipment_id} entry={e} index={i} />
        ))}
      </ol>
    </aside>
  );
}
