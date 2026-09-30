import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// jsdom lacks these; the app only feature-detects them.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as MediaQueryList;
}
if (typeof globalThis.ImageData === "undefined") {
  // Both constructor forms: (width, height) and (data, width, height).
  class ImageDataPolyfill {
    readonly data: Uint8ClampedArray;
    readonly width: number;
    readonly height: number;
    constructor(a: number | Uint8ClampedArray, b: number, c?: number) {
      this.data = typeof a === "number" ? new Uint8ClampedArray(a * b * 4) : a;
      this.width = typeof a === "number" ? a : b;
      this.height = typeof a === "number" ? b : (c ?? a.length / 4 / b);
    }
  }
  (globalThis as unknown as { ImageData: unknown }).ImageData = ImageDataPolyfill;
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => {};
