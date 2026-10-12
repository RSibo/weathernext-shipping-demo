import clsx from "clsx";
import { Pause, Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { laneView, truckPlan } from "../map/layers";
import { clock, relHours, weekday, zoneAbbr } from "../lib/time";
import { selectEntry, selectRun, useStore } from "../store";

const HOUR = 3600;
/** Label every N hours: the smallest step that leaves room for "18:00" between labels. */
const LABEL_STEPS = [3, 6, 12, 24];
const LABEL_MIN_PX = 46;

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function TimeScrubber({ className }: { className?: string }) {
  const wind = useStore((s) => s.forecasts.wind_mph);
  const time = useStore((s) => s.time);
  const playing = useStore((s) => s.playing);
  const setTime = useStore((s) => s.setTime);
  const togglePlay = useStore((s) => s.togglePlay);
  const entry = useStore(selectEntry);
  const run = useStore(selectRun);

  const tz = entry?.shipment.timezone ?? "Australia/Sydney";
  const t0 = wind?.initTime ?? 0;
  const t1 = wind ? wind.times[wind.times.length - 1] : 1;
  const pct = (t: number) => `${(((t - t0) / (t1 - t0)) * 100).toFixed(3)}%`;
  const [trackRef, trackW] = useWidth<HTMLDivElement>();

  const ticks = useMemo(() => {
    if (!wind || !trackW) return [];
    const pxPerHour = trackW / ((t1 - t0) / HOUR);
    const step = LABEL_STEPS.find((n) => n * pxPerHour >= LABEL_MIN_PX) ?? 24;
    const minor = pxPerHour >= 7 ? 1 : 3;
    const out: { t: number; major: boolean; label?: string }[] = [];
    for (let t = Math.ceil(t0 / HOUR) * HOUR; t <= t1; t += HOUR) {
      const hh = clock(t, tz).slice(0, 2);
      const h = Number(hh);
      if (h % minor !== 0) continue;
      const major = h % step === 0;
      out.push({ t, major, label: major ? (h === 0 ? weekday(t, tz) : `${hh}:00`) : undefined });
    }
    return out;
  }, [wind, t0, t1, tz, trackW]);

  const lane = useMemo(() => {
    if (!entry || !wind) return null;
    const { route } = laneView(entry, run);
    const plan = truckPlan(entry, run, wind.nowTime);
    const baseDepart = truckPlan(entry, undefined, wind.nowTime).depart;
    const segs = run.weather?.assessment ?? entry.assessment;
    const shownRoute = plan.route ?? route;
    const arrive = shownRoute ? plan.depart + shownRoute.path_t_s[shownRoute.path_t_s.length - 1] : undefined;
    const rerouted = run.advisory?.status === "REROUTE" || run.advisory?.status === "HOLD";
    return {
      depart: plan.depart,
      arrive,
      breaches: rerouted ? [] : (segs?.segments ?? []).map((s) => ({ a: baseDepart + s.start_t_s, b: baseDepart + s.end_t_s, window: s.window, place: s.place })),
      near: rerouted ? [] : (segs?.near_segments ?? []).map((s) => ({ a: baseDepart + s.start_t_s, b: baseDepart + s.end_t_s })),
    };
  }, [entry, run, wind]);

  if (!wind) return null;
  const rel = time - wind.nowTime;
  return (
    <div className={clsx("glass flex items-center gap-4 rounded-panel px-4 py-3", className)} data-testid="scrubber">
      <button
        type="button"
        onClick={() => togglePlay()}
        className="flex size-10 shrink-0 items-center justify-center rounded-full bg-ink-1 text-surface-0 transition-transform hover:scale-105 active:scale-95"
        aria-label={playing ? "Pause forecast playback" : "Play the forecast forward"}
        title="Play / pause (Space)"
      >
        {playing ? <Pause className="size-4 fill-current" aria-hidden /> : <Play className="size-4 translate-x-px fill-current" aria-hidden />}
      </button>
      <div className="shrink-0 whitespace-nowrap">
        <div className="tabular text-xl leading-none font-semibold tracking-tight text-ink-1">
          {weekday(time, tz)} {clock(time, tz)}
          <span className="ml-1 text-xs font-medium text-ink-4">{zoneAbbr(time, tz)}</span>
        </div>
        <div className="tabular mt-1 text-2xs text-ink-4">{Math.abs(rel) < 60 ? "now" : `${relHours(rel)} from now`}</div>
      </div>

      <div ref={trackRef} className="relative h-12 min-w-0 flex-1 select-none">
        {/* hour grid */}
        <div className="absolute inset-x-0 top-0 h-7">
          {ticks.map((k) => (
            <span key={k.t} className={clsx("absolute bottom-0 w-px", k.major ? "h-3 bg-ink-4/80" : "h-1.5 bg-ink-4/35")} style={{ left: pct(k.t) }} />
          ))}
          <div className="absolute inset-x-0 bottom-0 h-px bg-line-strong" />
          {/* forecast windows the selected lane breaches, at the hours the truck is there */}
          {lane?.near.map((n, i) => (
            <span key={`n${i}`} className="absolute bottom-0 h-2 rounded-sm bg-monitor/80" style={{ left: pct(n.a), width: `max(3px, calc(${pct(n.b)} - ${pct(n.a)}))` }} />
          ))}
          {lane?.breaches.map((b, i) => (
            <span key={`b${i}`} className="absolute bottom-0 h-2.5 rounded-sm bg-hold" style={{ left: pct(b.a), width: `max(3px, calc(${pct(b.b)} - ${pct(b.a)}))` }} title={`Over the limit ${b.window} near ${b.place}`} />
          ))}
          {/* now */}
          <span className="absolute top-0 bottom-0 w-px bg-ink-2" style={{ left: pct(wind.nowTime) }} />
          <span className="absolute top-0 -translate-x-1/2 rounded-sm bg-ink-2 px-1 text-[0.5625rem] leading-3 font-bold tracking-wider text-surface-0 uppercase" style={{ left: pct(wind.nowTime) }}>
            now
          </span>
        </div>
        {/* the truck's trip */}
        {lane?.arrive && (
          <div className="absolute top-8 h-1 rounded-full bg-ink-3/50" style={{ left: pct(lane.depart), width: `calc(${pct(Math.min(lane.arrive, t1))} - ${pct(lane.depart)})` }} title="This truck on the road">
            <span className="absolute -top-[3px] -left-[3px] size-2.5 rounded-full border-2 border-surface-1 bg-ink-2" />
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-3">
          {ticks
            .filter((k) => k.label)
            .map((k) => (
              <span key={k.t} className="tabular absolute -translate-x-1/2 text-[0.625rem] leading-none text-ink-4" style={{ left: pct(k.t) }}>
                {k.label}
              </span>
            ))}
        </div>
        {/* playhead: starts below the NOW pill so the two never overlap */}
        <div className="pointer-events-none absolute top-3.5 bottom-3 w-0.5 -translate-x-1/2 rounded-full bg-ink-1 shadow-[0_0_0_3px_rgb(13_13_13/0.6)]" style={{ left: pct(time) }}>
          <span className="absolute top-[0.5rem] left-1/2 size-3 -translate-x-1/2 rounded-full border-2 border-surface-0 bg-ink-1" />
        </div>
        <input
          type="range"
          aria-label="Forecast time"
          aria-valuetext={`${weekday(time, tz)} ${clock(time, tz)} ${zoneAbbr(time, tz)}`}
          className="scrub absolute inset-0 h-full w-full opacity-0"
          min={t0}
          max={t1}
          step={300}
          value={time}
          onChange={(e) => setTime(Number(e.target.value))}
          onPointerDown={() => togglePlay(false)}
        />
      </div>
    </div>
  );
}
