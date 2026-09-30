import { useEffect } from "react";
import { isActive } from "../lib/runReducer";
import { selectRun, useStore } from "../store";

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Presenter shortcuts. Space never re-clicks the focused button (that would re-run the agent). */
export function useHotkeys() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const typing = el && (TYPING.has(el.tagName) || el.isContentEditable);
      const s = useStore.getState();
      if (e.key === "Escape") {
        if (s.help) s.setHelp(false);
        else if (s.attract) s.setAttract(false);
        else if (isActive(selectRun(s))) s.stopRun();
        return;
      }
      if (typing && !(el instanceof HTMLInputElement && el.type === "range" && e.key === " ")) return;
      if (s.help && e.key !== "?") return;
      const entries = s.fleet?.entries ?? [];
      const hour = 3600;
      switch (e.key) {
        case " ":
          e.preventDefault();
          s.togglePlay();
          break;
        case "ArrowRight":
        case "ArrowLeft":
          if (el instanceof HTMLInputElement) return;
          e.preventDefault();
          s.togglePlay(false);
          s.animateTimeTo(Math.round(s.time / hour) * hour + (e.key === "ArrowRight" ? hour : -hour), 300);
          break;
        case "r":
        case "R":
          void s.runAgent();
          break;
        case "g":
        case "G":
          if (s.selectedId && s.config?.golden.includes(s.selectedId)) void s.runAgent(undefined, { golden: true });
          break;
        case "s":
        case "S":
          void s.rescan();
          break;
        case "a":
        case "A":
          s.setAttract(true);
          break;
        case "0":
          s.reframe("fleet");
          break;
        case "?":
          s.setHelp(!s.help);
          break;
        default:
          if (/^[1-9]$/.test(e.key)) {
            const entry = entries[Number(e.key) - 1];
            if (entry) s.select(entry.shipment.shipment_id);
          }
      }
    };
    // Swallow Space's keyup too: buttons activate on keyup.
    const onKeyUp = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.key === " " && el && !TYPING.has(el.tagName)) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);
}
