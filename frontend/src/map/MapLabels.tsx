import clsx from "clsx";
import { AdvancedMarker, CollisionBehavior } from "@vis.gl/react-google-maps";
import { Clock3, Route, TriangleAlert } from "lucide-react";
import { useMemo } from "react";
import { farthestFrom, positionAt, worstSegment } from "../lib/geo";
import { RISK_COLOR } from "../lib/palette";
import { recommendedRoute } from "../lib/runReducer";
import { selectEntry, selectRun, useStore } from "../store";
import { truckStates } from "./layers";

const TOP = ["50%", "100%"] as [string, string];
const LEFT = ["0%", "50%"] as [string, string];

/** HTML chips pinned to the map: they name what the lines mean, in the facts' own words. */
export function MapLabels() {
  const entry = useStore(selectEntry);
  const run = useStore(selectRun);
  const time = useStore((s) => s.time);
  const wind = useStore((s) => s.forecasts.wind_mph);
  const stage = useStore((s) => s.stage);
  const playing = useStore((s) => s.playing);

  const rec = recommendedRoute(run);
  const rerouted = run.advisory?.status === "REROUTE" && rec;
  const chosen = run.detour?.candidates.find((c) => c.route === rec);
  const segs = run.weather?.assessment.segments ?? entry?.assessment?.segments ?? [];
  const breach = !rerouted && run.advisory?.status !== "HOLD" ? worstSegment(segs) : undefined;
  const hold = run.advisory?.status === "HOLD" ? run.delay?.options.find((o) => o.option_id === run.advisory?.chosen_option_id) : undefined;
  const baseline = run.route ?? entry?.route;
  // Pinned where the detour departs furthest from the old route, but clear of where the truck sits at
  // the moment the story shows (the worst of the storm), so the chip doesn't jump while scrubbing.
  const recAt = useMemo(() => {
    if (!rerouted || !rec) return null;
    const worst = worstSegment(run.weather?.assessment.segments ?? []);
    const truck = worst ? positionAt(rec, (worst.start_t_s + worst.end_t_s) / 2).lonlat : undefined;
    return farthestFrom(rec.path, baseline?.path ?? [], truck);
  }, [rerouted, rec, baseline, run.weather]);

  const me = useMemo(
    () => (entry && wind ? truckStates({ entries: [entry], selectedId: entry.shipment.shipment_id, run, time, nowTime: wind.nowTime, wind })[0] : undefined),
    [entry, run, time, wind],
  );
  if (!entry) return null;
  const limit = entry.shipment.wind_threshold_mph;

  return (
    <>
      {breach && (
        <AdvancedMarker position={{ lat: breach.peak_lat, lng: breach.peak_lon }} anchorPoint={TOP} zIndex={20} collisionBehavior={CollisionBehavior.REQUIRED}>
          <div className={clsx("glass-chip mb-3 max-w-64 rounded-control px-2.5 py-1.5", stage && "text-base")} data-testid="label-breach">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-hold-ink">
              <TriangleAlert className="size-3.5" aria-hidden />
              Over {limit} mph · p90 {breach.peak_wind}
            </div>
            <div className="tabular text-2xs text-ink-3">
              {breach.window} · near {breach.place}
            </div>
          </div>
        </AdvancedMarker>
      )}
      {rerouted && recAt && (
        <AdvancedMarker position={{ lat: recAt[1], lng: recAt[0] }} anchorPoint={TOP} zIndex={21} collisionBehavior={CollisionBehavior.REQUIRED}>
          <div className="glass-chip mb-3 rounded-control px-2.5 py-1.5" data-testid="label-recommended">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-ink-1">
              <Route className="size-3.5 text-reroute-ink" aria-hidden />
              Recommended · {chosen?.label ?? rec!.description}
            </div>
            {chosen && <div className="tabular text-2xs text-ink-3">{[chosen.delta_duration, chosen.delta_distance].filter(Boolean).join(" · ")}</div>}
          </div>
        </AdvancedMarker>
      )}
      {hold && (
        <AdvancedMarker position={{ lat: entry.shipment.origin.lat, lng: entry.shipment.origin.lon }} anchorPoint={LEFT} zIndex={21} collisionBehavior={CollisionBehavior.REQUIRED}>
          <div className="glass-chip ml-4 rounded-control px-2.5 py-1.5" data-testid="label-hold">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-ink-1">
              <Clock3 className="size-3.5 text-hold-ink" aria-hidden />
              Hold · depart {hold.depart}
            </div>
            <div className="text-2xs text-ink-3">{hold.shift} later, after the wind eases</div>
          </div>
        </AdvancedMarker>
      )}
      {/* Once there's an advisory the route chips carry the story; the truck's chip returns during playback. */}
      {me && me.wind !== null && me.pos.state === "driving" && (playing || !run.advisory) && (
        <AdvancedMarker position={{ lat: me.pos.lonlat[1], lng: me.pos.lonlat[0] }} anchorPoint={LEFT} zIndex={30} collisionBehavior={CollisionBehavior.REQUIRED}>
          <div className={clsx("glass-chip tabular flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-2 text-xs font-semibold text-ink-1", stage ? "ml-9" : "ml-6")} data-testid="label-truck">
            <span className="size-2 rounded-full" style={{ background: RISK_COLOR[me.risk] }} aria-hidden />
            {Math.round(me.wind)} mph
            <span className="font-normal text-ink-4">here</span>
          </div>
        </AdvancedMarker>
      )}
    </>
  );
}
