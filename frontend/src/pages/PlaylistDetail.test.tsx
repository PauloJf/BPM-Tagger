import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { PlaylistTrack } from "../lib/types";

// PlaylistDetail pulls in router/query/auth/player context; mock them so the page
// renders in isolation and the header playback actions can be driven directly.
const h = vi.hoisted(() => ({
  tracks: [] as unknown[],
  playlist: {} as Record<string, unknown>,
  grabber: { enabled: false, spotify: { connected: false } } as Record<string, unknown>,
  role: "admin" as string | null,
  // Recorded player calls.
  played: [] as Array<{ tracks: unknown[]; shuffle?: boolean }>,
  enqueued: [] as unknown[][],
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
  useSearchParams: () => [new URLSearchParams("id=1")],
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: () => {} }),
  useQuery: () => ({ data: { playlist: h.playlist, tracks: h.tracks }, isLoading: false }),
  useMutation: () => ({ mutate: () => {}, isPending: false }),
}));

vi.mock("../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn(), del: vi.fn() } }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role }) }));
vi.mock("../hooks/useGrabberStatus", () => ({ useGrabberStatus: () => ({ data: h.grabber }) }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PlaylistSuggestions", () => ({ default: () => null }));
vi.mock("../components/AddToPlaylistMenu", () => ({ default: () => null }));
vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    playQueue: (tracks: unknown[], _i: number, opts?: { shuffle?: boolean }) =>
      h.played.push({ tracks, shuffle: opts?.shuffle }),
    enqueueMany: (tracks: unknown[]) => h.enqueued.push(tracks),
  }),
}));

// Import after the mocks are registered.
import PlaylistDetail from "./PlaylistDetail";

function t(over: Partial<PlaylistTrack>): PlaylistTrack {
  return {
    id: 1, source_track_id: null, spotify_track_id: null, position: 0,
    title: "T", artist: "", album: "", album_artist: "", duration_ms: null,
    isrc: null, track_no: null, cover_url: null, match_status: "have",
    matched_file_path: null, derived_status: "have", is_new: 0,
    first_seen_at: null, removed_at: null, ...over,
  };
}

const have = (n: number, over: Partial<PlaylistTrack> = {}) =>
  t({ id: n, position: n, title: `Have${n}`, derived_status: "have",
      matched_file_path: `/music/${n}.mp3`, ...over });

const counts = (over: Record<string, unknown> = {}) => ({
  id: 1, name: "PL", source: "spotify", have_count: 1, missing_count: 0,
  queued_count: 0, removed_count: 0, track_count: 1, last_synced_at: null,
  image_url: null, ...over,
});

const btn = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

beforeEach(() => {
  cleanup();
  h.tracks = [];
  h.playlist = counts();
  h.grabber = { enabled: false, spotify: { connected: false } };
  h.role = "admin";
  h.played = [];
  h.enqueued = [];
});

describe("PlaylistDetail — playback actions", () => {
  it("Play queues only the library-backed rows, in listed order", () => {
    h.tracks = [
      have(1),
      t({ id: 2, position: 1, title: "Missing", derived_status: "missing", match_status: "missing" }),
      t({ id: 3, position: 2, title: "Queued", derived_status: "queued", match_status: "missing" }),
      have(4),
      // A 'have' row with no matched file is unplayable — there's no file to stream.
      t({ id: 5, position: 4, title: "Orphan", derived_status: "have", matched_file_path: null }),
      t({ id: 6, position: 5, title: "Gone", derived_status: "removed", removed_at: "2026-01-01" }),
    ];
    h.playlist = counts({ have_count: 3, missing_count: 1, track_count: 6 });
    render(<PlaylistDetail />);

    fireEvent.click(btn(/^Play/));
    expect(h.played).toHaveLength(1);
    expect(h.played[0].tracks).toEqual([
      { path: "/music/1.mp3", title: "Have1", artist: "", bpm: null, loudnessLufs: null },
      { path: "/music/4.mp3", title: "Have4", artist: "", bpm: null, loudnessLufs: null },
    ]);
    expect(h.played[0].shuffle).toBe(false);
  });

  it("carries the library track's BPM, artist and loudness into the queue", () => {
    // loudnessLufs is what drives volume levelling — a playlist queue without it
    // would play un-levelled next to album/library queues.
    h.tracks = [have(1, { artist: "Source", local_artist: "Library",
                          local_bpm: 128.5, local_loudness_lufs: -8.5 })];
    render(<PlaylistDetail />);

    fireEvent.click(btn(/^Play/));
    expect(h.played[0].tracks).toEqual([
      { path: "/music/1.mp3", title: "Have1", artist: "Library", bpm: 128.5, loudnessLufs: -8.5 },
    ]);
  });

  it("falls back to the filename when a row has no title", () => {
    h.tracks = [have(1, { title: "" })];
    render(<PlaylistDetail />);
    fireEvent.click(btn(/^Play/));
    expect((h.played[0].tracks[0] as { title: string }).title).toBe("1.mp3");
  });

  it("Shuffle passes { shuffle: true }", () => {
    h.tracks = [have(1), have(2)];
    render(<PlaylistDetail />);
    fireEvent.click(btn(/^Shuffle/));
    expect(h.played).toHaveLength(1);
    expect(h.played[0].shuffle).toBe(true);
    expect(h.played[0].tracks).toHaveLength(2);
  });

  it("Add to queue appends the whole batch through enqueueMany", () => {
    // enqueueMany, not a loop over enqueue — the latter drops all but the last.
    h.tracks = [have(1), have(2), have(3)];
    render(<PlaylistDetail />);
    fireEvent.click(btn(/Add to queue/));
    expect(h.enqueued).toHaveLength(1);
    expect(h.enqueued[0]).toHaveLength(3);
    expect(h.played).toHaveLength(0);   // appends, never takes over
  });

  it("disables all three when nothing is in the library yet, and says why", () => {
    h.tracks = [t({ id: 1, title: "Missing", derived_status: "missing", match_status: "missing" })];
    h.playlist = counts({ have_count: 0, missing_count: 1, track_count: 1 });
    render(<PlaylistDetail />);

    for (const name of [/^Play/, /^Shuffle/, /Add to queue/]) {
      expect(btn(name).disabled).toBe(true);
      expect(btn(name).title).toMatch(/in your library yet/i);
    }
  });

  it("shows the playable count when it differs from the rows listed", () => {
    // The 50-tracks-but-12-matched case: a bare "Play" would look broken.
    h.tracks = [
      have(1), have(2),
      t({ id: 3, title: "M1", derived_status: "missing", match_status: "missing" }),
      t({ id: 4, title: "M2", derived_status: "missing", match_status: "missing" }),
    ];
    render(<PlaylistDetail />);
    expect(btn(/^Play \(2\)/)).toBeTruthy();
    expect(btn(/^Shuffle \(2\)/)).toBeTruthy();
  });

  it("omits the count when every listed row is playable (e.g. the Have tab)", () => {
    h.tracks = [have(1), have(2)];
    render(<PlaylistDetail />);
    expect(btn(/^Play$/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Play \(/ })).toBeNull();
  });
});

describe("PlaylistDetail — the grabber action is named for what it does", () => {
  it('reads "Download missing", not "Enqueue missing"', () => {
    // Two unrelated meanings of "queue" would otherwise sit on one page: audio
    // playback vs the download queue. The endpoint keeps its original name.
    h.tracks = [have(1)];
    h.playlist = counts({ have_count: 1, missing_count: 4, track_count: 5 });
    h.grabber = { enabled: true, spotify: { connected: true } };
    render(<PlaylistDetail />);
    expect(btn(/Download missing \(4\)/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Enqueue missing/ })).toBeNull();
  });
});
