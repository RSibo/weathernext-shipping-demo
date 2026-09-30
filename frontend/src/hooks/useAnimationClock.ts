import { useEffect, useState } from "react";

/** A requestAnimationFrame clock (performance.now) that only runs while `active`. */
export function useAnimationClock(active: boolean): number {
  const [t, setT] = useState(() => performance.now());
  useEffect(() => {
    if (!active) return;
    let id = requestAnimationFrame(function tick(now) {
      setT(now);
      id = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(id);
  }, [active]);
  return t;
}
