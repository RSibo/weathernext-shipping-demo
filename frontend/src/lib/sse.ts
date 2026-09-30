/**
 * A WHATWG-conformant-enough SSE parser (HTML Standard §9.2.6, "Interpreting an event stream").
 *
 * Copied from the k8s-copilot harvest/sse-contract package. A pure `(chunk) => frame[]` parser, so a
 * test can drive it at any chunk boundary, including one byte at a time, without a browser.
 *
 * Two edge cases a hand-rolled parser gets wrong in production and a happy-path test never
 * exercises:
 *
 * 1. A `\r` at the very end of a chunk always terminates a line right now — per spec a lone CR is
 *    a line ending on its own, and a `\r` immediately followed by `\r` or by nothing yet (chunk
 *    boundary) can't be part of a CRLF pair either way, so the ending is already known. The one
 *    thing that's *not* yet known is whether the next chunk's first character is the `\n` half of
 *    a CRLF pair that this same `\r` already accounted for. So the ending is resolved immediately
 *    (normalized to `\n`, frames can close on it in the same call), and only a one-bit flag —
 *    "swallow a leading `\n` on the next push" — carries across the boundary. This is what lets a
 *    frame whose terminating blank line is spelled with lone CRs (`...\r\r`) close within a single
 *    `push()` call instead of only ever resolving on the *following* one.
 * 2. Multi-byte UTF-8 characters can be split across a chunk boundary. `TextDecoderStream` handles
 *    this correctly (it buffers incomplete byte sequences internally); a bare `TextDecoder` used
 *    without `{ stream: true }` on every chunk would not.
 */

export interface RawSSEFrame {
  event: string;
  data: string;
}

/** Incremental parser: feed decoded text chunks, get back zero or more complete frames. */
export class SSEParser {
  private buffer = "";
  // Set when the buffer ends in a `\r` that's already been normalized to `\n`. If the *next*
  // push's chunk starts with `\n`, that character is the second half of a CRLF pair this `\r`
  // already accounted for — drop it rather than counting it as a second line ending.
  private pendingCRLFSwallow = false;

  push(chunk: string): RawSSEFrame[] {
    if (this.pendingCRLFSwallow && chunk.startsWith("\n")) {
      chunk = chunk.slice(1);
    }
    this.pendingCRLFSwallow = false;

    this.buffer += chunk;
    this.pendingCRLFSwallow = this.buffer.endsWith("\r");

    // CRLF | LF | lone CR are all valid line endings per the spec; normalize to LF before
    // splitting on the blank-line frame separator.
    const normalized = this.buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const blocks = normalized.split("\n\n");
    this.buffer = blocks.pop() ?? "";

    const frames: RawSSEFrame[] = [];
    for (const block of blocks) {
      const frame = parseBlock(block);
      if (frame) frames.push(frame);
    }
    return frames;
  }
}

function parseBlock(block: string): RawSSEFrame | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank line within a block, or a comment/keepalive

    const colonIdx = line.indexOf(":");
    const field = colonIdx === -1 ? line : line.slice(0, colonIdx);
    let value = colonIdx === -1 ? "" : line.slice(colonIdx + 1);
    // Exactly one leading space stripped after the field colon, per spec — `data:  x` retains
    // its second space, `data: x` and `data:x` are identical.
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
    // Other fields (`id`, `retry`) are accepted and ignored — this backend doesn't emit them.
  }

  if (dataLines.length === 0) return null; // a frame with no data field dispatches nothing
  return { event, data: dataLines.join("\n") };
}

/**
 * Drives an SSE parser over a fetch response body and yields decoded `{event, data}` frames.
 * `TextDecoderStream` is what makes multi-byte UTF-8 split across `read()` calls safe.
 */
export async function* rawSSEFrames(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<RawSSEFrame> {
  const parser = new SSEParser();
  // lib.dom.d.ts types TextDecoderStream's writable side as WritableStream<BufferSource>, which
  // TS 5.7+'s generic Uint8Array<ArrayBufferLike> no longer structurally satisfies — a lib
  // typing gap (TextDecoderStream genuinely accepts a Uint8Array stream at runtime), not a real
  // type error. Cast at the one call site rather than widening rawSSEFrames' public signature.
  const textStream = body.pipeThrough(
    new TextDecoderStream() as unknown as ReadableWritablePair<string, Uint8Array>,
  );
  const reader = textStream.getReader();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      if (!value) continue;
      for (const frame of parser.push(value)) {
        yield frame;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
