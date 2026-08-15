// The service worker's Range parsing (frontend/public/sw.js). Safari refuses
// media whose Range requests are answered with a plain 200, so the slicing
// math is the one part of the worker that must be exactly right — exercised
// here through the worker's test hook (self.__bpmSw).
import { beforeAll, describe, expect, it } from "vitest";

type ParseRange = (header: string | null, size: number) => { start: number; end: number } | null | "invalid";
let parseRange: ParseRange;

beforeAll(async () => {
  // Plain SW script: importing it registers no-op listeners on jsdom's window
  // and exposes the hook. No types on purpose — it must stay a classic script.
  // @ts-expect-error untyped service-worker script
  await import("../../public/sw.js");
  parseRange = (globalThis as unknown as { __bpmSw: { parseRange: ParseRange } }).__bpmSw.parseRange;
});

describe("parseRange", () => {
  it("returns null (full 200) when there is no Range header", () => {
    expect(parseRange(null, 1000)).toBeNull();
    expect(parseRange("", 1000)).toBeNull();
  });

  it("parses a closed range, clamping the end to the resource size", () => {
    expect(parseRange("bytes=0-499", 1000)).toEqual({ start: 0, end: 499 });
    expect(parseRange("bytes=500-999", 1000)).toEqual({ start: 500, end: 999 });
    expect(parseRange("bytes=500-2000", 1000)).toEqual({ start: 500, end: 999 });
    // Safari's probe request.
    expect(parseRange("bytes=0-1", 1000)).toEqual({ start: 0, end: 1 });
  });

  it("parses an open range (bytes=N-) to the end of the resource", () => {
    expect(parseRange("bytes=200-", 1000)).toEqual({ start: 200, end: 999 });
    expect(parseRange("bytes=0-", 1000)).toEqual({ start: 0, end: 999 });
  });

  it("parses a suffix range (bytes=-N) as the last N bytes", () => {
    expect(parseRange("bytes=-100", 1000)).toEqual({ start: 900, end: 999 });
    // A suffix longer than the resource means the whole resource.
    expect(parseRange("bytes=-5000", 1000)).toEqual({ start: 0, end: 999 });
  });

  it("flags unsatisfiable ranges (416)", () => {
    expect(parseRange("bytes=1000-", 1000)).toBe("invalid");
    expect(parseRange("bytes=1500-1600", 1000)).toBe("invalid");
    expect(parseRange("bytes=-0", 1000)).toBe("invalid");
  });

  it("treats malformed and multi-range headers as no range (full 200)", () => {
    expect(parseRange("bytes=0-499,600-999", 1000)).toBeNull();
    expect(parseRange("items=0-499", 1000)).toBeNull();
    expect(parseRange("bytes=-", 1000)).toBeNull();
    expect(parseRange("garbage", 1000)).toBeNull();
  });

  it("serves nothing special for an empty resource", () => {
    expect(parseRange("bytes=0-1", 0)).toBeNull();
  });
});
