import clsx from "clsx";
import { CloudRain, Wind } from "lucide-react";
import { FIELD_CLASSES } from "../lib/field";
import { FIELD_ALPHA, FIELD_RAMP, INK, RISK_COLOR, STATUS_COLOR } from "../lib/palette";
import { selectEntry, selectRun, useStore } from "../store";
import { Segmented } from "./ui";

function Swatch({ color, dashed, width = 3 }: { color: string; dashed?: boolean; width?: number }) {
  return (
    <svg width="22" height="8" aria-hidden className="shrink-0">
      <line x1="2" y1="4" x2="20" y2="4" stroke="#0d0d0d" strokeWidth={width + 2} strokeLinecap="round" />
      <line x1="2" y1="4" x2="20" y2="4" stroke={color} strokeWidth={width} strokeLinecap="round" strokeDasharray={dashed ? "3 3" : undefined} />
    </svg>
  );
}

export function Legend({ className }: { className?: string }) {
  const fieldVar = useStore((s) => s.fieldVar);
  const setFieldVar = useStore((s) => s.setFieldVar);
  const entry = useStore(selectEntry);
  const run = useStore(selectRun);
  const c = FIELD_CLASSES[fieldVar];
  const limit = entry?.shipment.wind_threshold_mph;
  const rerouted = run.advisory?.status === "REROUTE";
  const searched = Boolean(run.detour);
  const n = c.bins.length;
  // The ramp spans bins[0]..bins[n-1] + one more step so the last band has width.
  const lo = c.bins[0];
  const hi = c.bins[n - 1] + (c.bins[n - 1] - c.bins[n - 2]);
  const x = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <div className={clsx("glass w-[17.5rem] rounded-card px-3 pt-2.5 pb-3 text-2xs", className)} aria-label="Map legend" data-testid="legend">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-ink-1">WeatherNext 3</span>
        <Segmented
          label="Forecast layer"
          value={fieldVar}
          onChange={(v) => void setFieldVar(v)}
          options={[
            { value: "wind_mph", label: <><Wind className="size-3.5" aria-hidden /> Wind</> },
            { value: "precip_mm_hr", label: <><CloudRain className="size-3.5" aria-hidden /> Rain</> },
          ]}
        />
      </div>
      <p className="mt-1 text-ink-3">{c.label}</p>
      <div className="relative mt-2 h-2 overflow-hidden rounded-full">
        <div className="absolute inset-0 flex">
          {FIELD_RAMP.map((col, i) => (
            <span key={col} className="h-full flex-1" style={{ background: col, opacity: Math.min(1, FIELD_ALPHA[i] + 0.2) }} />
          ))}
        </div>
      </div>
      <div className="tabular relative mt-1 h-3 text-[0.625rem] text-ink-4">
        {c.bins.map((b, i) => (
          <span key={b} className={clsx("absolute", i === 0 ? "" : "-translate-x-1/2")} style={{ left: x(b) }}>
            {b}
          </span>
        ))}
        <span className="absolute right-0">{c.unit}</span>
      </div>
      {fieldVar === "wind_mph" && (
        <p className="mt-1.5 text-[0.625rem] leading-snug text-ink-4">
          Contours at <span className="text-ink-2">35 mph</span> (empty trailers) and <span className="text-ink-2">45 mph</span> (loaded).
          {limit !== undefined && <> This truck's limit: <span className="font-semibold text-ink-1">{limit} mph</span>.</>}
        </p>
      )}
      <div className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-2.5 text-ink-3">
        {rerouted ? (
          <>
            <span className="flex items-center gap-1.5"><Swatch color="#ffffff" width={4} />Recommended</span>
            <span className="flex items-center gap-1.5"><Swatch color="#898781" dashed />Avoided</span>
            <span className="flex items-center gap-1.5"><Swatch color={RISK_COLOR.breach} />Over limit</span>
          </>
        ) : (
          <>
            <span className="flex items-center gap-1.5"><Swatch color={INK} />Below limit</span>
            <span className="flex items-center gap-1.5"><Swatch color={RISK_COLOR.near} />Near limit</span>
            <span className="flex items-center gap-1.5"><Swatch color={RISK_COLOR.breach} />Over limit</span>
          </>
        )}
        {searched && <span className="flex items-center gap-1.5"><Swatch color={STATUS_COLOR.HOLD} dashed width={2} />Unsafe option</span>}
      </div>
    </div>
  );
}
