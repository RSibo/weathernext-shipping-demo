import { useId, useMemo, useState } from "react";
import { RISK_COLOR } from "../lib/palette";
import type { SampleView } from "../types";

/**
 * Wind the truck will meet along its route (x = km, y = p90 mph at each point's own ETA) against
 * the cargo's limit. The part above the limit is filled red, the part within 15% of it amber.
 */
export function RiskStrip({ samples, limit, height = 28, className }: { samples: SampleView[]; limit: number; height?: number; className?: string }) {
  const clip = useId();
  const [hover, setHover] = useState<number | null>(null);
  const W = 280;
  const H = height;
  const geo = useMemo(() => {
    const pts = samples.filter((s) => s.wind !== undefined && s.wind !== null);
    if (pts.length < 2) return null;
    const km1 = pts[pts.length - 1].km || 1;
    const top = Math.max(limit * 1.3, ...pts.map((p) => p.wind!));
    const x = (km: number) => (km / km1) * W;
    const y = (v: number) => H - 2 - (v / top) * (H - 4);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.km).toFixed(1)},${y(p.wind!).toFixed(1)}`).join("");
    const area = `${line}L${W},${H}L0,${H}Z`;
    return { pts, x, y, line, area, yLimit: y(limit), yNear: y(0.85 * limit), km1 };
  }, [samples, limit, H]);

  if (!geo) return <div className={className} style={{ height: H }} />;
  const hp = hover === null ? null : geo.pts[hover];
  return (
    <div className={`relative ${className ?? ""}`}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="block w-full overflow-visible"
        style={{ height: H }}
        role="img"
        aria-label={`Wind along the route against the ${limit} mph limit`}
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const km = ((e.clientX - r.left) / r.width) * geo.km1;
          let best = 0;
          for (let i = 1; i < geo.pts.length; i++) if (Math.abs(geo.pts[i].km - km) < Math.abs(geo.pts[best].km - km)) best = i;
          setHover(best);
        }}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <clipPath id={`${clip}-hi`}>
            <rect x="0" y="0" width={W} height={Math.max(0, geo.yLimit)} />
          </clipPath>
          <clipPath id={`${clip}-near`}>
            <rect x="0" y={geo.yLimit} width={W} height={Math.max(0, geo.yNear - geo.yLimit)} />
          </clipPath>
        </defs>
        <path d={geo.area} fill="rgb(255 255 255 / 0.07)" />
        <path d={geo.area} fill={RISK_COLOR.near} opacity={0.55} clipPath={`url(#${clip}-near)`} />
        <path d={geo.area} fill={RISK_COLOR.breach} opacity={0.9} clipPath={`url(#${clip}-hi)`} />
        <path d={geo.line} fill="none" stroke="#c3c2b7" strokeWidth={1.25} vectorEffect="non-scaling-stroke" />
        <line x1={0} x2={W} y1={geo.yLimit} y2={geo.yLimit} stroke="#e6e8eb" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" opacity={0.7} />
        {hp && <line x1={geo.x(hp.km)} x2={geo.x(hp.km)} y1={0} y2={H} stroke="#fff" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
      </svg>
      {hp && (
        <div className="glass-chip pointer-events-none absolute -top-1 z-10 -translate-x-1/2 -translate-y-full rounded-chip px-2 py-1 text-2xs whitespace-nowrap text-ink-1" style={{ left: `${(hp.km / geo.km1) * 100}%` }}>
          <span className="tabular font-semibold">{Math.round(hp.wind!)} mph</span>
          <span className="text-ink-3"> · km {Math.round(hp.km)}</span>
        </div>
      )}
    </div>
  );
}
