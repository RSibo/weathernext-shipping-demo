import { useMemo } from "react";
import { useAnimationClock } from "../hooks/useAnimationClock";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { fieldImage, forecastBounds } from "../lib/field";
import { selectRun, useStore } from "../store";
import { needsClock, type SceneInput } from "./layers";

const EMPTY: never[] = [];

/** Everything the map draws, gathered from the store. `animate` adds the render clock. */
export function useScene(animate: boolean): SceneInput | null {
  const entries = useStore((s) => s.fleet?.entries ?? EMPTY);
  const selectedId = useStore((s) => s.selectedId);
  const hoverId = useStore((s) => s.hoverId);
  const run = useStore(selectRun);
  const time = useStore((s) => s.time);
  const wind = useStore((s) => s.forecasts.wind_mph);
  const shown = useStore((s) => s.forecasts[s.fieldVar] ?? s.forecasts.wind_mph);
  const stage = useStore((s) => s.stage);
  const reducedMotion = useReducedMotion();
  const clock = useAnimationClock(animate && needsClock({ reducedMotion, selectedId }));

  // The field only changes visibly every couple of minutes of story time; don't rebuild it per frame.
  const qt = Math.round(time / 120) * 120;
  const field = useMemo(() => (shown ? { forecast: shown, image: fieldImage(shown, qt), bounds: forecastBounds(shown) } : undefined), [shown, qt]);

  if (!wind) return null;
  return { entries, selectedId, hoverId, run, time, nowTime: wind.nowTime, clock, field, wind, stage, reducedMotion };
}
