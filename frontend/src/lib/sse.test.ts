import { describe, expect, it } from "vitest";
import { SSEParser } from "./sse";

/**
 * Adapted from the parent `k8s-copilot` repo's `frontend/src/lib/sse.test.ts`. One describe block
 * was dropped on the way here — "against the real captured live-run byte stream" — because it
 * replays actual `k8s-copilot` production bytes (kubectl commands, citation URLs, the literal
 * "Scope Lock Active" string) from that repo's `artifacts/`, which is domain content this package
 * has no business shipping. Everything below is protocol-level and carries no such dependency.
 */

/** Feeds a full string through the parser at a fixed chunk size and returns every frame. */
function parseAtChunkSize(text: string, chunkSize: number) {
  const parser = new SSEParser();
  const frames = [];
  for (let i = 0; i < text.length; i += chunkSize) {
    frames.push(...parser.push(text.slice(i, i + chunkSize)));
  }
  return frames;
}

describe("SSEParser — basic framing", () => {
  it("parses a single named event with one data line", () => {
    const frames = parseAtChunkSize("event: telemetry\ndata: {\"a\":1}\n\n", 9999);
    expect(frames).toEqual([{ event: "telemetry", data: '{"a":1}' }]);
  });

  it("joins multi-line data with newlines", () => {
    const frames = parseAtChunkSize("event: thinking\ndata: line1\ndata: line2\n\n", 9999);
    expect(frames[0].data).toBe("line1\nline2");
  });

  it("ignores comment/keepalive lines", () => {
    const frames = parseAtChunkSize(": keepalive\n\nevent: done\ndata: {}\n\n", 9999);
    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe("done");
  });

  it("strips exactly one leading space after the field colon", () => {
    // `data: x` and `data:x` are identical; `data:  x` keeps its second space.
    const a = parseAtChunkSize("event: e\ndata: x\n\n", 9999);
    const b = parseAtChunkSize("event: e\ndata:x\n\n", 9999);
    const c = parseAtChunkSize("event: e\ndata:  x\n\n", 9999);
    expect(a[0].data).toBe("x");
    expect(b[0].data).toBe("x");
    expect(c[0].data).toBe(" x");
  });

  it("discards a trailing incomplete frame until the closing blank line arrives", () => {
    const parser = new SSEParser();
    expect(parser.push("event: done\ndata: {}")).toEqual([]);
    expect(parser.push("\n\n")).toEqual([{ event: "done", data: "{}" }]);
  });

  it("dispatches nothing for a frame with no data field", () => {
    const frames = parseAtChunkSize("event: pointless\n\n", 9999);
    expect(frames).toEqual([]);
  });
});

describe("SSEParser — line endings", () => {
  const CASES: [string, string][] = [
    ["LF", "event: done\ndata: {}\n\n"],
    ["CRLF", "event: done\r\ndata: {}\r\n\r\n"],
    ["lone CR", "event: done\rdata: {}\r\r"],
  ];

  for (const [name, text] of CASES) {
    it(`handles ${name} line endings`, () => {
      const frames = parseAtChunkSize(text, 9999);
      expect(frames).toEqual([{ event: "done", data: "{}" }]);
    });
  }

  it("holds a chunk ending in a lone \\r rather than guessing what it is", () => {
    // The #1 hand-rolled-SSE-parser bug: a chunk boundary landing right after \r must not be
    // treated as a line ending until we know whether the next byte is \n (CRLF) or not (lone CR).
    const parser = new SSEParser();
    expect(parser.push("event: done\ndata: {}\r")).toEqual([]);
    // Completing the CRLF pair, then the blank line.
    expect(parser.push("\n\r\n")).toEqual([{ event: "done", data: "{}" }]);
  });

  it("resolves a held \\r as a lone CR line ending when the next chunk is not \\n", () => {
    const parser = new SSEParser();
    expect(parser.push("event: done\rdata: {}\r")).toEqual([]);
    expect(parser.push("\rmore")).toEqual([{ event: "done", data: "{}" }]);
  });
});

describe("SSEParser — chunk boundaries, including 1 byte at a time", () => {
  const TEXT =
    "event: citations\ndata: {\"urls\":[\"a\"]}\n\n" +
    "event: thinking\ndata: {\"node\":\"planner\",\"text\":\"hi\",\"partial\":false}\n\n" +
    "event: done\ndata: {\"plan\":null}\n\n";

  const EXPECTED = [
    { event: "citations", data: '{"urls":["a"]}' },
    { event: "thinking", data: '{"node":"planner","text":"hi","partial":false}' },
    { event: "done", data: '{"plan":null}' },
  ];

  for (const size of [1, 2, 3, 7, 64, 997]) {
    it(`produces identical frames at chunk size ${size}`, () => {
      expect(parseAtChunkSize(TEXT, size)).toEqual(EXPECTED);
    });
  }
});

describe("SSEParser — multi-byte UTF-8 split across a chunk boundary", () => {
  it("is only safe when fed via TextDecoderStream, not raw string slicing", () => {
    // This test documents the reason `rawSSEFrames()` uses TextDecoderStream rather than a bare
    // TextDecoder: SSEParser itself operates on already-decoded strings (JS strings are UTF-16
    // internally), so splitting a *string* mid-character isn't representable the way splitting
    // *bytes* mid-character is. The real risk lives one layer down, in the byte-to-text decode —
    // covered by using the platform's own TextDecoderStream rather than reimplementing it.
    const text = "event: thinking\ndata: {\"text\":\"café\"}\n\n";
    const frames = parseAtChunkSize(text, 1);
    expect(JSON.parse(frames[0].data).text).toBe("café");
  });
});

describe("SSEParser — malformed input does not kill the stream", () => {
  it("still frames a block whose data is not valid JSON (JSON.parse happens one layer up)", () => {
    const frames = parseAtChunkSize("event: done\ndata: not-json{\n\n", 9999);
    expect(frames).toEqual([{ event: "done", data: "not-json{" }]);
  });
});
