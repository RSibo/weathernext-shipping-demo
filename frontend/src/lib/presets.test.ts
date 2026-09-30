import { describe, expect, it } from "vitest";
import { parsePresets } from "./presets";

describe("parsePresets", () => {
  it("defaults to the public-URL behaviour: attract on, nothing forced", () => {
    expect(parsePresets("")).toEqual({
      shipment: undefined, mode: undefined, hours: undefined, tab: undefined, stage: false, golden: undefined,
      run: false, attract: true, nostream: false, clean: false,
    });
  });

  it("reads the presenter presets", () => {
    const p = parsePresets("?shipment=shp-2001&golden=1&stage=1&attract=0&t=2.5&tab=details&mode=replay&nostream=true");
    expect(p).toMatchObject({ shipment: "SHP-2001", golden: 1, stage: true, attract: false, hours: 2.5, tab: "details", mode: "replay", nostream: true });
  });

  it("clamps the replay speed and ignores values it does not know", () => {
    expect(parsePresets("?golden=99").golden).toBe(16);
    expect(parsePresets("?golden=0").golden).toBe(1);
    expect(parsePresets("?golden=abc").golden).toBe(1);
    const junk = parsePresets("?shipment=<script>&mode=prod&tab=admin&t=soon");
    expect(junk).toMatchObject({ shipment: undefined, mode: undefined, tab: undefined, hours: undefined });
  });
});
