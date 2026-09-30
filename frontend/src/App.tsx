import clsx from "clsx";
import { useEffect } from "react";
import { AgentPanel } from "./components/AgentPanel";
import { FleetRail } from "./components/FleetRail";
import { Legend } from "./components/Legend";
import { AttractBanner, BootScreen, HelpOverlay, Toast } from "./components/Overlays";
import { TimeScrubber } from "./components/TimeScrubber";
import { TopBar } from "./components/TopBar";
import { useAttract } from "./hooks/useAttract";
import { useHotkeys } from "./hooks/useHotkeys";
import { presets } from "./lib/presets";
import { MapStage } from "./map/MapStage";
import { selectRun, useStore } from "./store";

/** One-shot URL presets once the fleet has loaded: ?golden=1 plays the recorded run, ?run=1 runs live. */
function useAutostart() {
  const ready = useStore((s) => s.boot === "ready");
  useEffect(() => {
    if (!ready) return;
    const s = useStore.getState();
    const hasGolden = Boolean(s.selectedId && s.config?.golden.includes(s.selectedId));
    if (presets.golden && hasGolden) void s.runAgent(undefined, { golden: true, speed: presets.golden });
    else if (presets.run) void s.runAgent();
    // Phone visitors from the QR code get the story without having to find the controls.
    else if (window.innerWidth < 768 && hasGolden && !presets.clean) void s.runAgent(undefined, { golden: true });
  }, [ready]);
}

export function App() {
  const boot = useStore((s) => s.boot);
  const bootError = useStore((s) => s.bootError);
  const start = useStore((s) => s.start);
  const stage = useStore((s) => s.stage);
  const phase = useStore((s) => selectRun(s).phase);
  useEffect(() => {
    void start();
  }, [start]);
  useHotkeys();
  useAttract();
  useAutostart();

  if (boot !== "ready") return <BootScreen error={boot === "error" ? (bootError ?? "Unknown error") : undefined} onRetry={() => void start()} />;

  return (
    <div className="flex min-h-dvh flex-col bg-surface-0 lg:h-dvh">
      <TopBar />
      <main className="relative flex flex-1 flex-col gap-3 p-3 lg:block lg:min-h-0 lg:gap-0 lg:p-0">
        {/* Panels stop 2.25rem above the map's bottom edge so Google's logo and attribution stay visible. */}
        <div className="relative h-[46vh] min-h-72 overflow-hidden rounded-panel lg:absolute lg:inset-0 lg:h-auto lg:rounded-none" data-testid="map">
          <MapStage />
          <Legend className="absolute top-3 left-3 z-10 max-lg:hidden lg:top-4 lg:left-[22rem]" />
        </div>
        <FleetRail className="z-10 max-lg:order-3 lg:absolute lg:top-4 lg:bottom-9 lg:left-4 lg:w-80" />
        <AgentPanel className={clsx("z-10 max-lg:order-2 max-lg:min-h-[70vh] lg:absolute lg:top-4 lg:right-4 lg:bottom-9", stage ? "lg:w-[30rem]" : "lg:w-[27.5rem]")} />
        <TimeScrubber className={clsx("z-10 max-lg:order-1 lg:absolute lg:bottom-9 lg:left-[22rem]", stage ? "lg:right-[32rem]" : "lg:right-[29.5rem]")} />
      </main>
      {/* For the screenshot tool and E2E tests: the selected run's phase. */}
      <span hidden data-testid="run-phase" data-phase={phase} />
      <AttractBanner />
      <HelpOverlay />
      <Toast />
    </div>
  );
}
