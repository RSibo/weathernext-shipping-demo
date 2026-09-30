import { APILoadingStatus, APIProvider, ColorScheme, Map, RenderingType, useApiLoadingStatus } from "@vis.gl/react-google-maps";
import { useEffect, useMemo } from "react";
import { selectRun, useStore } from "../store";
import { fitCamera, frameBounds, panelPadding } from "./camera";
import { DeckOverlay } from "./DeckOverlay";
import { FallbackStage } from "./FallbackStage";
import { Framer } from "./Framer";
import { MapLabels } from "./MapLabels";

function GoogleStage({ mapId }: { mapId: string }) {
  const status = useApiLoadingStatus();
  const setMapsFailed = useStore((s) => s.setMapsFailed);
  useEffect(() => {
    if (status === APILoadingStatus.AUTH_FAILURE || status === APILoadingStatus.FAILED) setMapsFailed();
  }, [status, setMapsFailed]);

  // First paint already framed on the selected lane; the Framer takes over from there.
  const initial = useMemo(() => {
    const s = useStore.getState();
    const b = frameBounds("lane", s.fleet?.entries ?? [], s.selectedId, selectRun(s));
    const c = b ? fitCamera(b, window.innerWidth, window.innerHeight - 56, panelPadding(window.innerWidth, s.stage)) : { lon: -108, lat: 40.5, zoom: 5 };
    return { center: { lng: c.lon, lat: c.lat }, zoom: c.zoom + 1 };
  }, []);

  return (
    <Map
      className="absolute inset-0"
      mapId={mapId}
      colorScheme={ColorScheme.DARK}
      renderingType={RenderingType.VECTOR}
      defaultCenter={initial.center}
      defaultZoom={initial.zoom}
      disableDefaultUI
      gestureHandling="greedy"
      clickableIcons={false}
      keyboardShortcuts={false}
      isFractionalZoomEnabled
      backgroundColor="#0d0d0d"
      tilt={0}
      heading={0}
    >
      <DeckOverlay />
      <Framer />
      <MapLabels />
    </Map>
  );
}

export function MapStage() {
  const config = useStore((s) => s.config);
  const failed = useStore((s) => s.mapsFailed);
  const setMapsFailed = useStore((s) => s.setMapsFailed);
  if (!config) return null;
  if (!config.maps_api_key || failed) return <FallbackStage />;
  return (
    <APIProvider apiKey={config.maps_api_key} onError={() => setMapsFailed()} solutionChannel="">
      <GoogleStage mapId={config.map_id || "DEMO_MAP_ID"} />
    </APIProvider>
  );
}
