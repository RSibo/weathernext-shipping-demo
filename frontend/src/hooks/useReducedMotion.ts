import { useSyncExternalStore } from "react";

const query = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;

export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (cb) => {
      query?.addEventListener("change", cb);
      return () => query?.removeEventListener("change", cb);
    },
    () => query?.matches ?? false,
    () => false,
  );
}
