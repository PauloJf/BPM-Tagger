import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// The Similar panel's two sections: "From your library" (offline, always
// playable, cadence-aware under a lock) above "From Deezer", and one Queue all
// that batches both through enqueueMany.

const h = vi.hoisted(() => ({
  lib: [] as unknown[],
  deezer: [] as unknown[],
  queue: [] as Array<{ path: string }>,
  tempoLock: null as null | { target: number; octave: boolean; stretchLimitPct: number },
  enqueued: [] as unknown[],
  enqueuedMany: [] as unknown[][],
  keys: [] as unknown[][],
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey, enabled }: { queryKey: unknown[]; enabled?: boolean }) => {
    h.keys.push(queryKey);
    if (enabled === false) return { data: undefined, isLoading: false };
    if (queryKey[0] === "related-library") return { data: { tracks: h.lib }, isLoading: false };
    if (queryKey[0] === "related-tracks") return { data: { tracks: h.deezer }, isLoading: false };
    return { data: undefined, isLoading: false };
  },
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    queue: h.queue, tempoLock: h.tempoLock, endPreview() {},
    enqueue(t: unknown) { h.enqueued.push(t); },
    enqueueMany(ts: unknown[]) { h.enqueuedMany.push(ts); },
  }),
}));
vi.mock("../hooks/useGrabberStatus", () => ({ useGrabberStatus: () => ({ data: { enabled: false } }) }));
vi.mock("../hooks/useSuggestionQueue", () => ({ useSuggestionQueue: () => ({ mutate() {}, isPending: false }) }));
vi.mock("./trackBits", () => ({ PreviewButton: () => null }));

import QueueSimilar from "./QueueSimilar";

const libTrack = (path: string, reason: "artist" | "tempo", bpm = 170) =>
  ({ path, title: path.slice(1), artist: "Ann", bpm, starred: false, loudness_lufs: null, reason });

beforeEach(() => {
  h.lib = []; h.deezer = []; h.queue = []; h.tempoLock = null;
  h.enqueued = []; h.enqueuedMany = []; h.keys = [];
});
afterEach(() => cleanup());

describe("QueueSimilar — from your library", () => {
  it("lists library picks with why they were chosen", () => {
    h.lib = [libTrack("/one", "artist"), libTrack("/two", "tempo")];
    render(<QueueSimilar artist="Ann" path="/seed" />);
    expect(screen.getByText("From your library")).toBeTruthy();
    expect(screen.getByText("From Deezer")).toBeTruthy();
    const rows = screen.getAllByTestId("similar-library-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("same artist");
    expect(rows[1].textContent).toContain("similar tempo");
  });

  it("queues one pick, and marks what's already queued", () => {
    h.lib = [libTrack("/one", "artist"), libTrack("/two", "tempo")];
    h.queue = [{ path: "/two" }];
    render(<QueueSimilar artist="Ann" path="/seed" />);
    const rows = screen.getAllByTestId("similar-library-row");
    expect(rows[1].textContent).toContain("in queue");
    fireEvent.click(rows[0].querySelector("button")!);
    expect(h.enqueued).toEqual([expect.objectContaining({ path: "/one", title: "one" })]);
  });

  it("Queue all batches library + Deezer matches in ONE enqueueMany, deduped", () => {
    h.lib = [libTrack("/one", "artist")];
    h.deezer = [
      { dz_track_id: "1", title: "Dup", artist: "Ann", album: "", duration_ms: null, cover_url: "",
        preview_url: "", in_library: true, file_path: "/one", bpm: 170 },
      { dz_track_id: "2", title: "Three", artist: "Ann", album: "", duration_ms: null, cover_url: "",
        preview_url: "", in_library: true, file_path: "/three", bpm: 170 },
    ];
    render(<QueueSimilar artist="Ann" path="/seed" />);
    fireEvent.click(screen.getByText(/Queue all · 2/));
    expect(h.enqueuedMany).toHaveLength(1);
    expect((h.enqueuedMany[0] as Array<{ path: string }>).map((t) => t.path)).toEqual(["/one", "/three"]);
    expect(h.enqueued).toHaveLength(0);
  });

  it("asks for cadence-fitting picks while a run is locked", () => {
    h.tempoLock = { target: 165, octave: true, stretchLimitPct: 10 };
    render(<QueueSimilar artist="Ann" path="/seed" />);
    expect(h.keys).toContainEqual(["related-library", "/seed", 165, 10]);
  });

  it("has no library section without a path (e.g. a preview is playing)", () => {
    render(<QueueSimilar artist="Ann" />);
    expect(screen.queryByText("From your library")).toBeNull();
  });
});
