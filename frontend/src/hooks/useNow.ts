import { useEffect, useState } from "react";

/** performance.now(), refreshed every `ms` while `active` (live elapsed-time labels). */
export function useNow(active: boolean, ms = 100): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!active) return;
    setNow(performance.now());
    const id = setInterval(() => setNow(performance.now()), ms);
    return () => clearInterval(id);
  }, [active, ms]);
  return now;
}
