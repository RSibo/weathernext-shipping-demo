import { useEffect } from "react";
import { presets } from "../lib/presets";
import { isActive } from "../lib/runReducer";
import { selectRun, useStore } from "../store";

export const IDLE_MS = 60_000;
const DWELL_MS = 14_000;

/**
 * Idle attract mode for the public URL: after a minute untouched, play the recorded runs in turn
 * (hero first). Any input hands control back. Off on the presenter's screen (?stage=1) unless `A`.
 */
export function useAttract() {
  const attract = useStore((s) => s.attract);
  const ready = useStore((s) => s.boot === "ready");

  // Idle detection, and "any input takes control back".
  useEffect(() => {
    if (!ready) return;
    const auto = presets.attract && !presets.stage && !presets.clean;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      clearTimeout(timer);
      if (!auto) return;
      timer = setTimeout(() => {
        const s = useStore.getState();
        if (isActive(selectRun(s)) || s.help) return arm();
        s.setAttract(true);
      }, IDLE_MS);
    };
    const onInput = (e: Event) => {
      if (e instanceof KeyboardEvent && (e.key === "a" || e.key === "A")) return;
      if (useStore.getState().attract) useStore.getState().setAttract(false);
      arm();
    };
    const kinds = ["pointerdown", "keydown", "wheel", "touchstart"] as const;
    kinds.forEach((k) => window.addEventListener(k, onInput, { passive: true }));
    arm();
    return () => {
      clearTimeout(timer);
      kinds.forEach((k) => window.removeEventListener(k, onInput));
    };
  }, [ready]);

  // The loop itself.
  useEffect(() => {
    if (!attract) return;
    const s0 = useStore.getState();
    const golden = s0.config?.golden ?? [];
    if (!golden.length) return;
    const hero = s0.config?.hero_shipment;
    const ids = hero && golden.includes(hero) ? [hero, ...golden.filter((g) => g !== hero)] : golden;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let i = 0;
    const next = async () => {
      if (cancelled) return;
      await useStore.getState().runAgent(ids[i++ % ids.length], { golden: true });
      if (!cancelled) timer = setTimeout(next, DWELL_MS);
    };
    void next();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [attract]);
}
