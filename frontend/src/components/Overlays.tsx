import clsx from "clsx";
import { CircleCheck, Info, MousePointerClick, RotateCcw, WifiOff, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";
import { presets } from "../lib/presets";
import { useStore } from "../store";
import { Kbd } from "./ui";

const HOTKEYS: [string, string][] = [
  ["1 – 5", "Select a truck"],
  ["R", "Run the agent live"],
  ["G", "Play the recorded run"],
  ["S", "Scan the fleet"],
  ["Space", "Play / pause the forecast"],
  ["← →", "Step the forecast by an hour"],
  ["0", "Frame the whole fleet"],
  ["A", "Start the auto demo"],
  ["Esc", "Stop / close"],
];

export function HelpOverlay() {
  const open = useStore((s) => s.help);
  const setHelp = useStore((s) => s.setHelp);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-6 backdrop-blur-[2px]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={() => setHelp(false)}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="help-title"
            className="glass w-full max-w-md rounded-panel p-5"
            initial={{ y: 8, scale: 0.98 }}
            animate={{ y: 0, scale: 1 }}
            exit={{ y: 8, scale: 0.98 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 id="help-title" className="text-base font-semibold text-ink-1">
                Keyboard shortcuts
              </h2>
              <button ref={closeRef} type="button" className="btn btn-quiet size-8 !p-0" onClick={() => setHelp(false)} aria-label="Close">
                <X className="size-4" aria-hidden />
              </button>
            </div>
            <dl className="mt-4 divide-y divide-line">
              {HOTKEYS.map(([k, d]) => (
                <div key={k} className="flex items-center justify-between py-2 text-sm">
                  <dt className="text-ink-2">{d}</dt>
                  <dd>
                    <Kbd>{k}</Kbd>
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-xs leading-relaxed text-ink-4">
              Every number the agent shows comes from a tool result: WeatherNext 3 samples along the route and Google Maps Routes. Gemini decides and writes; code checks the decision and every figure before
              it reaches this screen.
            </p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Toast() {
  const toast = useStore((s) => s.toast);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => useStore.setState((s) => (s.toast?.id === toast.id ? { toast: undefined } : s)), 3600);
    return () => clearTimeout(t);
  }, [toast]);
  if (presets.clean) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-20 z-40 flex justify-center" role="status" aria-live="polite">
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            className="glass flex items-center gap-2 rounded-full px-4 py-2 text-sm text-ink-1"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
          >
            {toast.tone === "good" ? <CircleCheck className="size-4 text-clear-ink" aria-hidden /> : <Info className="size-4 text-ink-3" aria-hidden />}
            {toast.text}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function AttractBanner() {
  const attract = useStore((s) => s.attract);
  const setAttract = useStore((s) => s.setAttract);
  return (
    <AnimatePresence>
      {attract && (
        <motion.button
          type="button"
          onClick={() => setAttract(false)}
          className="glass fixed top-[4.5rem] left-1/2 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full py-1.5 pr-4 pl-2 text-sm text-ink-1"
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          data-testid="attract-banner"
        >
          <span className="flex items-center gap-1.5 rounded-full bg-accent px-2 py-0.5 text-2xs font-semibold tracking-wide text-white uppercase">
            <span className="size-1.5 animate-breathe rounded-full bg-white" /> Auto demo
          </span>
          <MousePointerClick className="size-4 text-ink-3" aria-hidden />
          Click anywhere to take control
        </motion.button>
      )}
    </AnimatePresence>
  );
}

function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <path d="M3 19c4-6 8-6 12 0s8 6 14 0" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
      <path d="M3 12c4-6 8-6 12 0s8 6 14 0" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" opacity=".45" />
      <circle cx="25" cy="24.5" r="3" fill="#3277cb" />
    </svg>
  );
}

export function BootScreen({ error, onRetry }: { error?: string; onRetry: () => void }) {
  return (
    <div className="flex h-dvh flex-col items-center justify-center gap-5 bg-surface-0 px-6 text-center" role={error ? "alert" : "status"}>
      <Mark className={clsx("size-12 text-ink-1", !error && "animate-breathe")} />
      {error ? (
        <>
          <div>
            <p className="flex items-center justify-center gap-2 text-base font-medium text-ink-1">
              <WifiOff className="size-4 text-hold-ink" aria-hidden /> The server didn't answer
            </p>
            <p className="mt-1 max-w-sm text-sm text-ink-3">{error}</p>
          </div>
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            <RotateCcw className="size-4" aria-hidden /> Try again
          </button>
        </>
      ) : (
        <div>
          <p className="shimmer-text text-sm font-medium">Loading the WeatherNext 3 forecast</p>
          <p className="mt-1 text-xs text-ink-4">36 hours of wind over the Mountain West</p>
        </div>
      )}
    </div>
  );
}

export { Mark };
