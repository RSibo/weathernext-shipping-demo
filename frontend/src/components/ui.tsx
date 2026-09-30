import clsx from "clsx";
import { CircleCheck, CircleHelp, CirclePause, Eye, Route, TriangleAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { Hint, Status } from "../types";

interface Meta {
  label: string;
  icon: LucideIcon;
  /** Text-safe tint for labels on dark surfaces. */
  ink: string;
  /** Full-strength status colour for fills, rings and dots. */
  color: string;
}

export const STATUS_META: Record<Status, Meta> = {
  CLEAR: { label: "Clear", icon: CircleCheck, ink: "text-clear-ink", color: "bg-clear" },
  MONITOR: { label: "Monitor", icon: Eye, ink: "text-monitor-ink", color: "bg-monitor" },
  REROUTE: { label: "Reroute", icon: Route, ink: "text-reroute-ink", color: "bg-reroute" },
  HOLD: { label: "Hold", icon: CirclePause, ink: "text-hold-ink", color: "bg-hold" },
};

export const HINT_META: Record<Hint, Meta> = {
  CLEAR: STATUS_META.CLEAR,
  MONITOR: STATUS_META.MONITOR,
  ACTION: { label: "Needs action", icon: TriangleAlert, ink: "text-hold-ink", color: "bg-hold" },
  UNKNOWN: { label: "No forecast", icon: CircleHelp, ink: "text-ink-3", color: "bg-ink-4" },
};

/** Status is never colour alone: icon + label + tint, sized for a projector at `lg`. */
export function StatusChip({ meta, size = "sm", className }: { meta: Meta; size?: "sm" | "lg"; className?: string }) {
  const Icon = meta.icon;
  return (
    <span
      className={clsx(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border border-current/30 bg-current/10 font-semibold tracking-wide uppercase",
        size === "sm" ? "h-6 px-2 text-2xs" : "h-8 px-3 text-sm",
        meta.ink,
        className,
      )}
    >
      <Icon className={size === "sm" ? "size-3.5" : "size-4.5"} strokeWidth={2.4} aria-hidden />
      {meta.label}
    </span>
  );
}

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return <kbd className={clsx("kbd", className)}>{children}</kbd>;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-control border border-line bg-surface-0/60 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={clsx(
            "inline-flex items-center gap-1.5 rounded-[calc(var(--radius-control)-2px)] font-medium transition-colors",
            size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3.5 text-sm",
            o.value === value ? "bg-surface-3 text-ink-1 shadow-card" : "text-ink-3 hover:text-ink-1",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={clsx("text-2xs font-semibold tracking-[0.08em] text-ink-4 uppercase", className)}>{children}</h3>;
}
