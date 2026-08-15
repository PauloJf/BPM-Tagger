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
  useQueryClient: () => ({ invalidateQueries: () => {}, setQueryData: () => {} }),
  useQuery: () => ({ data: { playlist: h.playlist, tracks: h.tracks }, isLoading: false }),
  // Run the real mutationFn so tests can assert on the request the page makes
  // (api is mocked below, so nothing leaves the process).
  useMutation: (opts: { mutationFn: (v: unknown) => unknown }) => ({
    mutate: (v: unknown) => opts.mutationFn(v),
    isPending: false,
  }),
}));

vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn(), del: vi.fn(),
    post: vi.fn(() => Promise.resolve({})),
    patch: vi.fn(() => Promise.resolve({})),
  },
  apiUpload: vi.fn(() => Promise.resolve({})),
}));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role }) }));
vi.mock("../hooks/useGrabberStatus", () => ({ useGrabberStatus: () => ({ data: h.grabber }) }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PlaylistSuggestions", () => ({ default: () => null }));
// The stats strip owns its own fetch (this file's useQuery mock answers every
// query with the track listing), so it's stubbed to a marker the role tests can
// look for. Its own behaviour is covered in PlaylistStats.test.tsx.
vi.mock("../components/PlaylistStats", () => ({
  default: ({ playlistId }: { playlistId: string }) =>
    <div data-testid="stats-strip">{playlistId}</div>,
}));
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
import { api } from "../lib/api";

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
  image_url: null, description: "", pinned: 0, ...over,
});

const btn = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

beforeEach(() => {
  cleanup();
  // Artwork defaults to shown; individual tests flip the stored preference.
  localStorage.clear();
  vi.mocked(api.patch).mockClear();
  vi.mocked(api.post).mockClear();
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

describe("PlaylistDetail — rename and description", () => {
  it("renames a local playlist through PATCH", () => {
    h.playlist = counts({ source: "local", name: "Old" });
    render(<PlaylistDetail />);

    fireEvent.click(btn(/Edit name/));
    const input = screen.getByLabelText("Playlist name") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  Long Run  " } });
    fireEvent.click(btn(/^Save$/));

    // Trimmed client-side too, so the optimistic header text matches the server's.
    expect(api.patch).toHaveBeenCalledWith("/api/playlists/1", { name: "Long Run" });
  });

  it("saves a rename on Enter and abandons it on Escape", () => {
    h.playlist = counts({ source: "local", name: "Old" });
    render(<PlaylistDetail />);

    fireEvent.click(btn(/Edit name/));
    fireEvent.keyDown(screen.getByLabelText("Playlist name"), { key: "Escape" });
    expect(screen.queryByLabelText("Playlist name")).toBeNull();
    expect(api.patch).not.toHaveBeenCalled();

    fireEvent.click(btn(/Edit name/));
    fireEvent.change(screen.getByLabelText("Playlist name"), { target: { value: "Via Enter" } });
    fireEvent.keyDown(screen.getByLabelText("Playlist name"), { key: "Enter" });
    expect(api.patch).toHaveBeenCalledWith("/api/playlists/1", { name: "Via Enter" });
  });

  it("never offers rename on a synced playlist — sync would overwrite it", () => {
    h.playlist = counts({ source: "spotify", name: "From Spotify" });
    render(<PlaylistDetail />);
    expect(screen.queryByRole("button", { name: /Edit name/ })).toBeNull();
  });

  it("hides both editors from a player", () => {
    h.role = "player";
    h.playlist = counts({ source: "local", name: "Mix" });
    render(<PlaylistDetail />);
    expect(screen.queryByRole("button", { name: /Edit name/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit description/ })).toBeNull();
  });

  it("edits the description on a synced playlist — sync never touches it", () => {
    h.playlist = counts({ source: "spotify", description: "" });
    render(<PlaylistDetail />);

    fireEvent.click(btn(/Edit description/));
    fireEvent.change(screen.getByLabelText("Playlist description"), { target: { value: "Tempo work" } });
    fireEvent.click(btn(/^Save$/));

    expect(api.patch).toHaveBeenCalledWith("/api/playlists/1", { description: "Tempo work" });
  });

  it("shows an existing description to a player, without an editor", () => {
    h.role = "player";
    h.playlist = counts({ description: "Threshold intervals" });
    render(<PlaylistDetail />);
    expect(screen.getByText("Threshold intervals")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Edit description/ })).toBeNull();
  });
});

describe("PlaylistDetail — track artwork", () => {
  const imgs = (c: HTMLElement) => Array.from(c.querySelectorAll("img")).map((i) => i.getAttribute("src"));

  it("shows a matched row's embedded art from the local file", () => {
    h.tracks = [have(1)];
    const { container } = render(<PlaylistDetail />);
    expect(imgs(container)).toContain("/api/track/cover?path=%2Fmusic%2F1.mp3");
    expect(container.querySelector(".pl-track-row--art")).toBeTruthy();
  });

  it("falls back to the source's own cover_url on an unmatched row", () => {
    h.tracks = [t({ id: 2, title: "Missing", derived_status: "missing",
                    match_status: "missing", cover_url: "https://i.scdn.co/image/abc" })];
    const { container } = render(<PlaylistDetail />);
    expect(imgs(container)).toContain("https://i.scdn.co/image/abc");
  });

  it("keeps the column aligned with a placeholder when a row has no art at all", () => {
    // A Navidrome row's cover_url is a bare coverArt id, not a URL — it 404s
    // into this same placeholder, so the grid must not collapse.
    h.tracks = [t({ id: 2, title: "Missing", derived_status: "missing",
                    match_status: "missing", cover_url: null })];
    const { container } = render(<PlaylistDetail />);
    expect(imgs(container)).toHaveLength(0);
    expect(container.querySelector(".art-thumb")).toBeTruthy();
    expect(container.querySelector(".pl-track-row--art")).toBeTruthy();
  });

  it("renders today's exact grid when artwork is toggled off", () => {
    localStorage.setItem("bpmtagger.showArtwork", "0");
    h.tracks = [have(1)];
    const { container } = render(<PlaylistDetail />);
    expect(imgs(container)).toHaveLength(0);
    expect(container.querySelector(".pl-track-row--art")).toBeNull();
    expect(container.querySelector(".pl-track-row")).toBeTruthy();
  });
});

describe("PlaylistDetail — local playlist cover", () => {
  it("renders the cover endpoint for a local playlist", () => {
    h.playlist = counts({ source: "local", image_url: null });
    const { container } = render(<PlaylistDetail />);
    const srcs = Array.from(container.querySelectorAll("img")).map((i) => i.getAttribute("src"));
    expect(srcs).toContain("/api/playlists/1/cover");
  });

  it("leaves a synced playlist on its source's image_url", () => {
    h.playlist = counts({ source: "spotify", image_url: "https://i.scdn.co/image/pl" });
    const { container } = render(<PlaylistDetail />);
    const srcs = Array.from(container.querySelectorAll("img")).map((i) => i.getAttribute("src"));
    expect(srcs).toContain("https://i.scdn.co/image/pl");
    expect(srcs.some((s) => s?.includes("/api/playlists/1/cover"))).toBe(false);
  });

  it("offers Set cover… only to an admin on a local playlist", () => {
    h.playlist = counts({ source: "local" });
    render(<PlaylistDetail />);
    expect(screen.getByRole("button", { name: /Set cover/ })).toBeTruthy();

    cleanup();
    h.role = "player";
    render(<PlaylistDetail />);
    expect(screen.queryByRole("button", { name: /Set cover/ })).toBeNull();

    cleanup();
    h.role = "admin";
    h.playlist = counts({ source: "spotify" });
    render(<PlaylistDetail />);
    expect(screen.queryByRole("button", { name: /Set cover/ })).toBeNull();
  });
});

describe("PlaylistDetail — sort and search", () => {
  const rowTitles = (c: HTMLElement) =>
    Array.from(c.querySelectorAll("[data-testid='pl-title']")).map((e) => e.textContent || "");

  const sortBy = (key: string) =>
    fireEvent.change(screen.getByLabelText("Sort tracks"), { target: { value: key } });

  const typeSearch = (q: string) =>
    fireEvent.change(screen.getByLabelText("Search this playlist"), { target: { value: q } });

  it("sorts by BPM with unanalyzed tracks last, and plays the visible order", () => {
    h.tracks = [
      have(1, { title: "Fast", local_bpm: 175 }),
      have(2, { title: "Unknown", local_bpm: null }),
      have(3, { title: "Slow", local_bpm: 120 }),
    ];
    const { container } = render(<PlaylistDetail />);
    sortBy("bpm");

    expect(rowTitles(container).filter((t) => /Fast|Slow|Unknown/.test(t)))
      .toEqual(["Slow", "Fast", "Unknown"]);
    // Playback follows what's on screen — the existing tab contract, extended.
    fireEvent.click(btn(/^Play/));
    expect(h.played[0].tracks.map((t) => (t as { title: string }).title))
      .toEqual(["Slow", "Fast", "Unknown"]);
  });

  it("sorts by title and by artist", () => {
    h.tracks = [
      have(1, { title: "Beta", local_artist: "Zed" }),
      have(2, { title: "Alpha", local_artist: "Ann" }),
    ];
    const { container } = render(<PlaylistDetail />);

    sortBy("title");
    expect(rowTitles(container).filter((t) => /Alpha|Beta/.test(t))).toEqual(["Alpha", "Beta"]);
    sortBy("artist");
    expect(rowTitles(container).filter((t) => /Alpha|Beta/.test(t))).toEqual(["Alpha", "Beta"]);
  });

  it("keeps the server's playlist order by default", () => {
    h.tracks = [have(1, { title: "Zed" }), have(2, { title: "Ann" })];
    const { container } = render(<PlaylistDetail />);
    expect(rowTitles(container).filter((t) => /Zed|Ann/.test(t))).toEqual(["Zed", "Ann"]);
  });

  it("narrows rows by search, and the play count follows", () => {
    h.tracks = [
      have(1, { title: "Runaway", local_artist: "Kanye" }),
      have(2, { title: "Sunrise", local_artist: "Norah" }),
      have(3, { title: "Other", local_artist: "Someone" }),
    ];
    const { container } = render(<PlaylistDetail />);
    typeSearch("run");

    expect(container.querySelectorAll(".pl-track-row")).toHaveLength(1);
    fireEvent.click(btn(/^Play/));
    expect(h.played[0].tracks).toHaveLength(1);
  });

  it("searches the source metadata too, not just the library's", () => {
    h.tracks = [t({ id: 9, title: "Ghost", artist: "SourceOnly",
                    derived_status: "missing", match_status: "missing" })];
    const { container } = render(<PlaylistDetail />);
    typeSearch("sourceonly");
    expect(container.querySelectorAll(".pl-track-row")).toHaveLength(1);
  });

  it("says the search hid everything, rather than claiming the playlist is empty", () => {
    h.tracks = [have(1, { title: "Runaway" })];
    render(<PlaylistDetail />);
    typeSearch("zzzz");
    expect(screen.getByText(/No tracks match your search/)).toBeTruthy();
  });
});

describe("PlaylistDetail — duplicate detection", () => {
  it("flags two rows pointing at the same library file", () => {
    h.tracks = [
      have(1, { title: "A", matched_file_path: "/music/dup.mp3" }),
      have(2, { title: "A again", matched_file_path: "/music/dup.mp3" }),
      have(3, { title: "B", matched_file_path: "/music/other.mp3" }),
    ];
    const { container } = render(<PlaylistDetail />);

    expect(screen.getByText(/⧉ 1 duplicate in this playlist/)).toBeTruthy();
    expect(container.querySelectorAll(".chip")).toBeTruthy();
    expect(screen.getAllByTitle(/same track/)).toHaveLength(2);   // one chip per clustered row
  });

  it("flags two rows sharing an ISRC", () => {
    h.tracks = [
      have(1, { title: "A", matched_file_path: "/music/1.mp3", isrc: "US1234567890" }),
      have(2, { title: "A alt", matched_file_path: "/music/2.mp3", isrc: "US1234567890" }),
    ];
    render(<PlaylistDetail />);
    expect(screen.getByText(/⧉ 1 duplicate in this playlist/)).toBeTruthy();
  });

  it("toggles a duplicates-only view", () => {
    h.tracks = [
      have(1, { title: "A", matched_file_path: "/music/dup.mp3" }),
      have(2, { title: "A again", matched_file_path: "/music/dup.mp3" }),
      have(3, { title: "B", matched_file_path: "/music/other.mp3" }),
    ];
    const { container } = render(<PlaylistDetail />);
    expect(container.querySelectorAll(".pl-track-row")).toHaveLength(3);

    fireEvent.click(btn(/Show duplicates only/));
    expect(container.querySelectorAll(".pl-track-row")).toHaveLength(2);
    fireEvent.click(btn(/Show all tracks/));
    expect(container.querySelectorAll(".pl-track-row")).toHaveLength(3);
  });

  it("does not treat two empty ISRCs as a duplicate", () => {
    h.tracks = [
      have(1, { title: "A", matched_file_path: "/music/1.mp3", isrc: null }),
      have(2, { title: "B", matched_file_path: "/music/2.mp3", isrc: "" }),
    ];
    render(<PlaylistDetail />);
    expect(screen.queryByText(/duplicate/)).toBeNull();
  });

  it("ignores tombstoned rows — a removed row isn't membership", () => {
    h.tracks = [
      have(1, { title: "A", matched_file_path: "/music/dup.mp3" }),
      t({ id: 2, position: 1, title: "A (gone)", matched_file_path: "/music/dup.mp3",
          derived_status: "removed", removed_at: "2026-01-01" }),
    ];
    render(<PlaylistDetail />);
    expect(screen.queryByText(/duplicate/)).toBeNull();
  });

  it("tells a synced playlist that its source owns membership", () => {
    h.playlist = counts({ source: "spotify" });
    h.tracks = [
      have(1, { title: "A", matched_file_path: "/music/dup.mp3" }),
      have(2, { title: "A again", matched_file_path: "/music/dup.mp3" }),
    ];
    render(<PlaylistDetail />);
    expect(screen.getByText(/the source owns this playlist's membership/)).toBeTruthy();
  });
});

describe("PlaylistDetail — reorder", () => {
  const local3 = () => {
    h.playlist = counts({ source: "local", have_count: 3, track_count: 3 });
    h.tracks = [have(0, { title: "A" }), have(1, { title: "B" }), have(2, { title: "C" })];
  };

  it("posts the whole new order when a row moves up", () => {
    local3();
    render(<PlaylistDetail />);
    fireEvent.click(btn(/Move B up/));
    // Ids come from have(n) → id n; B and A swap, C stays put.
    expect(api.post).toHaveBeenCalledWith("/api/playlists/1/reorder", { order: [1, 0, 2] });
  });

  it("posts the whole new order when a row moves down", () => {
    local3();
    render(<PlaylistDetail />);
    fireEvent.click(btn(/Move A down/));
    expect(api.post).toHaveBeenCalledWith("/api/playlists/1/reorder", { order: [1, 0, 2] });
  });

  it("cannot move the first row up or the last row down", () => {
    local3();
    render(<PlaylistDetail />);
    expect(btn(/Move A up/).disabled).toBe(true);
    expect(btn(/Move C down/).disabled).toBe(true);
    expect(btn(/Move B up/).disabled).toBe(false);
  });

  it("is offered only on a local playlist, to an admin", () => {
    local3();
    h.playlist = counts({ source: "spotify", have_count: 3, track_count: 3 });
    render(<PlaylistDetail />);
    expect(screen.queryByRole("button", { name: /Move .* up/ })).toBeNull();

    cleanup();
    local3();
    h.role = "player";
    render(<PlaylistDetail />);
    expect(screen.queryByRole("button", { name: /Move .* up/ })).toBeNull();
  });

  it("withdraws while the view is sorted, searched, or duplicates-only", () => {
    // Dropping a row into a projection of the playlist has no single meaning,
    // and the endpoint wants the complete order — which a filtered view isn't.
    local3();
    const { container } = render(<PlaylistDetail />);
    expect(container.querySelector(".player-queue-grip")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Sort tracks"), { target: { value: "title" } });
    expect(screen.queryByRole("button", { name: /Move .* up/ })).toBeNull();

    fireEvent.change(screen.getByLabelText("Sort tracks"), { target: { value: "position" } });
    expect(screen.queryByRole("button", { name: /Move B up/ })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Search this playlist"), { target: { value: "A" } });
    expect(screen.queryByRole("button", { name: /Move .* up/ })).toBeNull();
  });

  it("withdraws on a status tab — those rows are a subset of the playlist", () => {
    local3();
    render(<PlaylistDetail />);
    fireEvent.click(screen.getByRole("button", { name: "Have" }));
    expect(screen.queryByRole("button", { name: /Move .* up/ })).toBeNull();
  });
});

describe("PlaylistDetail — stats strip and per-row plays", () => {
  it("renders the stats strip for an admin and withholds it from a player", () => {
    h.tracks = [have(1)];
    render(<PlaylistDetail />);
    expect(screen.getByTestId("stats-strip").textContent).toBe("1");

    cleanup();
    // The endpoint is outside _PLAYER_ALLOWED, so asking would only earn a 403.
    h.role = "player";
    render(<PlaylistDetail />);
    expect(screen.queryByTestId("stats-strip")).toBeNull();
  });

  it("shows a matched row's play count beside its BPM and length", () => {
    h.tracks = [have(1, { local_bpm: 150, local_duration_ms: 185_000, local_play_count: 12 })];
    render(<PlaylistDetail />);
    expect(screen.getByText("150 BPM · 3:05 · 12 plays")).toBeTruthy();
  });

  it("singularizes one play, and stays silent at zero", () => {
    h.tracks = [have(1, { local_bpm: 150, local_play_count: 1 })];
    render(<PlaylistDetail />);
    expect(screen.getByText("150 BPM · 1 play")).toBeTruthy();

    cleanup();
    // An unplayed library must not grow a column of "0 plays".
    h.tracks = [have(2, { local_bpm: 150, local_play_count: 0 })];
    render(<PlaylistDetail />);
    expect(screen.getByText("150 BPM")).toBeTruthy();
    expect(screen.queryByText(/play/)).toBeNull();
  });

  it("never claims plays for an unmatched row — the count is the library file's", () => {
    h.tracks = [t({ id: 3, title: "Missing", derived_status: "missing",
                    match_status: "missing", local_play_count: 9 })];
    render(<PlaylistDetail />);
    expect(screen.queryByText(/play/)).toBeNull();
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
