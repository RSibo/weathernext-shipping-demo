import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { presets } from "./lib/presets";
import { useStore } from "./store";
import "./index.css";

// Dev-only handle for the screenshot and probe scripts.
if (import.meta.env.DEV) (window as unknown as { __store: typeof useStore }).__store = useStore;

if (presets.stage) document.documentElement.dataset.stage = "1";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
