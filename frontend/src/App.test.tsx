// Smoke test: the whole app boots against a stubbed backend and plays a recorded run through the
// real SSE parser, reducer and panels. Only the Google map is replaced (it needs a browser and a key).
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONFIG, FLEET, FORECAST, syntheticRun } from "./fixtures/synthetic";

vi.mock("./map/MapStage", () => ({ MapStage: () => <div data-testid="map-stub" /> }));

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

function sse(events: unknown[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(enc.encode(": keepalive\n\n"));
      for (const e of events) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`));
      c.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

const requests: string[] = [];

beforeEach(() => {
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.startsWith("/api/config")) return json(CONFIG);
      if (url.startsWith("/api/fleet")) return json(FLEET);
      if (url.startsWith("/api/forecast")) return json(FORECAST);
      if (url.startsWith("/api/golden/")) return sse(syntheticRun());
      return new Response("not found", { status: 404 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("boots into the fleet view, then plays a recorded run to a grounded advisory", async () => {
    const { App } = await import("./App");
    render(<App />);

    const card = await screen.findByTestId("fleet-SHP-9001");
    expect(within(card).getByText(/44 mph/)).toBeTruthy(); // the worst stretch, not the first one
    expect(screen.getByTestId("scan-context").textContent).toMatch(/2 stretches/);
    expect(screen.getByTestId("map-stub")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Play the recorded run" }));
    });
    await waitFor(() => expect(document.querySelector('[data-testid="run-phase"]')?.getAttribute("data-phase")).toBe("done"));
    expect(requests).toContain("/api/golden/SHP-9001?speed=1");
    expect(screen.getAllByText(/Reroute via I-98 E/).length).toBeGreaterThan(0);
    expect(screen.getByText("Recorded run")).toBeTruthy();
  });
});
