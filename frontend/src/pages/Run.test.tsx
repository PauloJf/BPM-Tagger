import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Run pulls in a lot of context/hooks; mock them so the page renders in
// isolation and we can drive `role`, `current`, the queue, and the playlist
// list per test via this hoisted, mutable holder.
const h = vi.hoisted(() => ({
  role: "admin" as string | null,
  fullAccess: true as boolean,
  current: null as null | { path: string; title: string; artist?: string; bpm?: number | null },
  orderedQueue: [] as Array<{ path: string; title: string; artist?: string; bpm?: number | null; starred?: boolean; fromPlaylist?: boolean }>,
  tempoLock: null as null | { target: number; octave: boolean; stretchLimitPct: number },
  playing: false,
  online: true,
  buffering: false,
  bufferedPct: 0,
  bufferInfo: undefined as undefined | { phase: string; pct: number; aheadSec: number; stalls: number; ready: number; net: number },
  runSource: null as number | "mine" | null,
  playlists: [] as Array<{ id: number; name: string; source: string; available: number; total: number; image_url: string | null }>,
  starred: [] as Array<[string, boolean]>,   // records setTrackStarred(path, on) calls
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));

vi.mock("@tanstack/react-query", () => ({
  keepPreviousData: undefined,
  useQueryClient: () => ({ setQueryData: () => {} }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const k = queryKey[0];
    if (k === "settings")
      return { data: { settings: {
        run_presets: [{ name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
                      { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 }],
        run_octave_fold: true, run_stretch_limit_pct: 15, run_queue_size: 20,
      } } };
    if (k === "run-playlists") return { data: { playlists: h.playlists } };
    // play_count present so tests can assert the mobile layout doesn't render it.
    if (k === "track-bpm") return { data: { track: { bpm: 120, locked: 0, play_count: 3 }, quality: null } };
    return { data: undefined };
  },
}));

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({})), post: vi.fn(() => Promise.resolve({})) },
  audioUrl: (p: string) => `/audio?path=${p}`,
}));
// fullAccess mirrors the real context's default (true until /api/me reports a
// restricted player user) — the "Whole library" and "All my music" source options
// are gated on it, so tests drive it via the holder.
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role, fullAccess: h.fullAccess }) }));
vi.mock("../lib/player", () => ({
  lockRate: () => 1,
  usePlayer: () => ({
    current: h.current, playing: h.playing, audioRef: { current: null },
    error: null, buffering: h.buffering, bufferedPct: h.bufferedPct, bufferInfo: h.bufferInfo, online: h.online,
    orderedQueue: h.orderedQueue, orderPos: 0, tempoLock: h.tempoLock, runSource: h.runSource,
    updateTrackBpm() {}, setTrackStarred(path: string, on: boolean) { h.starred.push([path, on]); },
    setTempoLock() {}, playQueue() {}, setRunSource() {},
    next() {}, prev() {}, toggle() {}, jumpTo() {},
  }),
}));
vi.mock("../lib/miniPlayer", () => ({ useMiniPlayer: () => ({ supported: false, isOpen: false, toggle() {} }) }));
vi.mock("../hooks/useTapTempo", () => ({ useTapTempo: () => ({ display: "—", taps: 0, canApply: false, onTap() {}, reset() {} }) }));
vi.mock("../hooks/useWaveform", () => ({ useWaveform: () => ({ loading: false }) }));
vi.mock("../hooks/useAudioTime", () => ({ useAudioTime: () => ({ time: 0, dur: 0 }), fmtTime: () => "0:00" }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../hooks/useCoverGlow", () => ({ useCoverGlow: () => ({ background: "transparent", opacity: 0 }) }));
vi.mock("../hooks/useIsMobile", () => ({ useIsMobile: () => true }));   // force the mobile layout
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/LyricsPanel", () => ({ LyricsPanel: () => null }));
vi.mock("../components/QueueSimilar", () => ({ default: () => null }));

// Import after the mocks are registered.
import Run, { pulsePeriodMs } from "./Run";
import { api } from "../lib/api";

const MODE_KEY = "bpm.run.mode";
const TARGET_KEY = "bpm.run.target";
const SOURCE_KEY = "bpm.run.source";

beforeEach(() => {
  cleanup();
  localStorage.clear();
  h.role = "admin";
  h.fullAccess = true;
  h.current = null;
  h.orderedQueue = [];
  h.tempoLock = null;
  h.playing = false;
  h.online = true;
  h.buffering = false;
  h.bufferedPct = 0;
  h.bufferInfo = undefined;
  h.runSource = null;
  h.starred = [];
  // A deliberately long name — the sort that used to spill across the cover art.
  h.playlists = [{ id: 1, name: "Long Playlist Name That Covered The Art", source: "spotify", available: 5, total: 10, image_url: null }];
});

describe("Run — mobile source picker placement", () => {
  it("puts the source picker inside the Queue view", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    const region = screen.getByTestId("queue-source");
    expect(region.querySelector("select[aria-label='Run source']")).toBeTruthy();
  });

  it("does NOT overlay a source picker on the cover during playback (presets mode)", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    // Previously a picker sat overlaid on the cover here; there must be none now.
    expect(screen.queryByLabelText("Run source")).toBeNull();
    expect(screen.queryByTestId("queue-source")).toBeNull();
  });

  it("offers 'All my music' to a scoped player with 2+ playlists (but not 'Whole library')", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.role = "player";
    h.fullAccess = false;
    h.playlists = [
      { id: 1, name: "Alpha", source: "spotify", available: 5, total: 10, image_url: null },
      { id: 2, name: "Beta", source: "local", available: 3, total: 3, image_url: null },
    ];
    render(<Run />);
    const select = screen.getByLabelText("Run source") as HTMLSelectElement;
    const opts = Array.from(select.options).map((o) => o.textContent);
    expect(opts).toContain("All my music");
    expect(opts).not.toContain("Whole library");   // scoped players never get it
  });

  it("hides 'All my music' when a scoped player has only one playlist", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.role = "player";
    h.fullAccess = false;
    h.playlists = [{ id: 1, name: "Alpha", source: "spotify", available: 5, total: 10, image_url: null }];
    render(<Run />);
    const select = screen.getByLabelText("Run source") as HTMLSelectElement;
    const opts = Array.from(select.options).map((o) => o.textContent);
    expect(opts).not.toContain("All my music");
  });

  it("still offers the source picker pre-run (nothing playing yet)", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = null;
    render(<Run />);
    expect(screen.getByLabelText("Run source")).toBeTruthy();
  });

  it("omits the source picker entirely when the library has no playlists", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.playlists = [];
    render(<Run />);
    expect(screen.queryByTestId("queue-source")).toBeNull();
    expect(screen.queryByLabelText("Run source")).toBeNull();
  });
});

describe("Run — force-tempo toggle placement (mobile)", () => {
  it("puts the force toggle inside the Queue view", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    const region = screen.getByTestId("queue-force");
    expect(region.textContent).toMatch(/FORCE TEMPO/);
  });

  it("does NOT show the force toggle on the main run page during playback (presets mode)", () => {
    // It used to sit under the presets and eat the vertical space that shoves the
    // cover off small screens once a track is playing.
    localStorage.setItem(MODE_KEY, "presets");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    expect(screen.queryByText(/FORCE TEMPO/)).toBeNull();
    expect(screen.queryByTestId("queue-force")).toBeNull();
  });

  it("still offers the force toggle pre-run on the main page (no cover yet)", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = null;
    render(<Run />);
    expect(screen.getByText(/FORCE TEMPO/)).toBeTruthy();
  });
});

describe("Run — mobile layout is flex-driven (no viewport math)", () => {
  it("the queue list scrolls internally and carries no hand-tuned dvh clamp", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = Array.from({ length: 40 }, (_, i) => ({ path: `/music/${i}.mp3`, title: `T${i}`, bpm: 120 }));
    render(<Run />);
    // Raw inline style so the assertion doesn't depend on jsdom's CSS parsing.
    const style = screen.getByTestId("queue-list").getAttribute("style") || "";
    expect(style).toContain("overflow-y: auto");
    // Regression guard: sizing now comes from the fixed-height flex column
    // (.run-mobile-fill), not from viewport arithmetic that drifted every time
    // a row was added or removed.
    expect(style).not.toContain("dvh");
  });

  it("renders the cover in the flexible slot when playing (presets mode)", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    expect(screen.getByTestId("cover-slot")).toBeTruthy();
  });

  it("drops the cover slot in the Queue view (the queue takes its space)", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    expect(screen.queryByTestId("cover-slot")).toBeNull();
  });

  it("hides the play count on mobile (vertical budget) — desktop-only detail", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    // The mocked track detail has play data, but the mobile layout must not
    // spend a line on it (useIsMobile is mocked true → mobile layout).
    expect(screen.queryByText(/play(s)?$/)).toBeNull();
  });
});

describe("Run — lock toggle mirrors the restored tempo lock (no reload desync)", () => {
  it("defaults ON for a fresh session (no restored queue)", () => {
    h.orderedQueue = [];
    h.tempoLock = null;
    render(<Run />);
    expect(screen.getByLabelText("BPM locked")).toBeTruthy();
    expect(screen.queryByLabelText("BPM unlocked")).toBeNull();
  });

  it("shows UNLOCKED when a restored run had its lock off", () => {
    // The reload-desync bug: queue restored, tempoLock null → the button used to
    // wrongly show locked while audio played native.
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.tempoLock = null;
    render(<Run />);
    expect(screen.getByLabelText("BPM unlocked")).toBeTruthy();
    expect(screen.queryByLabelText("BPM locked")).toBeNull();
  });

  it("shows LOCKED when a restored run had its lock on", () => {
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.tempoLock = { target: 155, octave: true, stretchLimitPct: 15 };
    render(<Run />);
    expect(screen.getByLabelText("BPM locked")).toBeTruthy();
    expect(screen.queryByLabelText("BPM unlocked")).toBeNull();
  });
});

describe("Run — connection visibility", () => {
  it("shows an offline banner when the connection is down", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.online = false;
    render(<Run />);
    expect(screen.getByText(/Offline — waiting for connection/)).toBeTruthy();
  });

  it("shows a buffering banner with percentage while stalled and online", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.online = true;
    h.buffering = true;
    h.bufferedPct = 42;
    render(<Run />);
    expect(screen.getByText(/Buffering · 42%/)).toBeTruthy();
  });

  it("shows detailed buffer diagnostics (phase, %, ahead, tries) when available", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.online = true;
    h.buffering = true;
    h.bufferInfo = { phase: "hold", pct: 63, aheadSec: 3.2, stalls: 2, ready: 2, net: 2 };
    render(<Run />);
    expect(screen.getByText(/Rebuffering · 63% · 3\.2s ahead · try 2/)).toBeTruthy();
  });

  it("does not show a buffering banner when offline (offline takes priority)", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.online = false;
    h.buffering = true;
    render(<Run />);
    expect(screen.queryByText(/Buffering/)).toBeNull();
    expect(screen.getByText(/Offline/)).toBeTruthy();
  });

  it("shows no connection banner when online and not buffering", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    expect(screen.queryByText(/Offline/)).toBeNull();
    expect(screen.queryByText(/Buffering/)).toBeNull();
  });

  it("floats the note above the waveform (inside the transport) while a track plays", () => {
    // Out-of-flow placement: a banner that grew the column used to push the
    // transport off the bottom of the phone screen.
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [h.current];
    h.buffering = true;
    render(<Run />);
    expect(screen.getByText(/Buffering/).closest(".run-transport")).toBeTruthy();
  });

  it("keeps the note inline pre-run (no transport exists yet)", () => {
    h.current = null;
    h.online = false;
    render(<Run />);
    const note = screen.getByText(/Offline — waiting for connection/);
    expect(note.closest(".run-transport")).toBeNull();
  });
});

describe("Run — queue star toggles come from the track, not the build response", () => {
  it("renders a star button per queued track with a known star state (incl. refilled)", () => {
    // queueInfo is null here (no build this session) — the old code derived stars
    // from queueInfo, so refilled/restored tracks showed no star button. Now the
    // row reads t.starred, so any track carrying star state gets a toggle.
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = [
      { path: "/a.mp3", title: "A", bpm: 120, starred: true },
      { path: "/b.mp3", title: "B", bpm: 120, starred: false },
      { path: "/c.mp3", title: "C", bpm: 120 },   // unknown star state → no button
    ];
    render(<Run />);
    expect(screen.getAllByLabelText("Unstar")).toHaveLength(1);   // the starred one
    expect(screen.getAllByLabelText("Star")).toHaveLength(1);     // the unstarred one
  });

  it("toggling a queued track's star updates the player queue (works for any row)", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/b.mp3", title: "B", bpm: 120 };
    h.orderedQueue = [{ path: "/b.mp3", title: "B", bpm: 120, starred: false }];
    render(<Run />);
    fireEvent.click(screen.getByLabelText("Star"));
    // Reflected through the player (not queueInfo), so refilled rows update too.
    expect(h.starred).toContainEqual(["/b.mp3", true]);
  });
});

describe("Run — library top-up tracks are visually dimmed in a playlist run", () => {
  // Rows are asserted by their (non-current) title span → up to the row div.
  // The current row carries a ▶ marker, so we assert only non-current rows.
  const rowOf = (title: string) => screen.getByText(title).closest("div") as HTMLElement;

  it("dims library top-ups but not the playlist's own tracks", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.runSource = 1;   // a playlist run is active
    h.current = { path: "/cur.mp3", title: "CurrentTrack", bpm: 120 };
    h.orderedQueue = [
      { path: "/cur.mp3", title: "CurrentTrack", bpm: 120, fromPlaylist: true },   // current (not asserted)
      { path: "/pl.mp3", title: "PlaylistTrack", bpm: 120, fromPlaylist: true },   // playlist's own → full
      { path: "/lib.mp3", title: "LibraryTopUp", bpm: 120, fromPlaylist: false },  // top-up → dimmed
    ];
    render(<Run />);
    expect(rowOf("LibraryTopUp").style.opacity).toBe("0.5");
    expect(rowOf("PlaylistTrack").style.opacity).toBe("1");
  });

  it("does not dim anything in a whole-library run (no playlist source)", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.runSource = null;   // library run — nothing is a "top-up"
    h.current = { path: "/a.mp3", title: "TrackA", bpm: 120 };
    h.orderedQueue = [
      { path: "/a.mp3", title: "TrackA", bpm: 120, fromPlaylist: false },
      { path: "/b.mp3", title: "TrackB", bpm: 120, fromPlaylist: false },
    ];
    render(<Run />);
    expect(rowOf("TrackB").style.opacity).toBe("1");
  });
});

describe("pulsePeriodMs — pulse tracks the cadence you hear", () => {
  it("locked + unclamped pulses at the target (shifted === target)", () => {
    expect(pulsePeriodMs(true, 150, 150, 150)).toBe(Math.round(60000 / 150)); // 400
  });
  it("locked + stretch-clamped pulses at the heard cadence, not the target", () => {
    // e.g. 60 BPM folded ×2 → 120, clamped to +15% → 138 heard.
    expect(pulsePeriodMs(true, 138, 60, 150)).toBe(Math.round(60000 / 138));  // 435
    expect(pulsePeriodMs(true, 138, 60, 150)).not.toBe(Math.round(60000 / 150));
  });
  it("unlocked pulses at the track's native BPM", () => {
    expect(pulsePeriodMs(false, 150, 75, 150)).toBe(Math.round(60000 / 75));  // 800
  });
  it("falls back to the target when values are missing", () => {
    expect(pulsePeriodMs(true, null, null, 150)).toBe(400);
  });
});

describe("Run — beat pulse shows the ×2 octave schematically", () => {
  const pulseMode = () => screen.getByTestId("beat-pulse").getAttribute("data-pulse");

  it("uses the two-step pulse when a locked track folds ×2 (beat at half cadence)", () => {
    localStorage.setItem(TARGET_KEY, "150");
    h.tempoLock = { target: 150, octave: true, stretchLimitPct: 15 };   // locked
    h.playing = true;
    h.current = { path: "/a.mp3", title: "A", bpm: 75 };                 // 75 → folds ×2 to 150
    render(<Run />);
    expect(pulseMode()).toBe("2x");
  });

  it("uses the single pulse when the track plays at native tempo (×1)", () => {
    localStorage.setItem(TARGET_KEY, "150");
    h.tempoLock = { target: 150, octave: true, stretchLimitPct: 15 };
    h.playing = true;
    h.current = { path: "/a.mp3", title: "A", bpm: 150 };                // ×1
    render(<Run />);
    expect(pulseMode()).toBe("1x");
  });

  it("does not double-time an unlocked run (plays native, no octave applied)", () => {
    localStorage.setItem(TARGET_KEY, "150");
    h.tempoLock = null;
    h.playing = true;
    h.current = { path: "/a.mp3", title: "A", bpm: 75 };
    h.orderedQueue = [h.current];   // restored queue + no lock → lockOn starts off
    render(<Run />);
    expect(pulseMode()).toBe("1x");
  });
});

describe("Run — changing the source mid-run prompts a Rebuild", () => {
  it("warns that the source changed after a build, until Rebuild", async () => {
    localStorage.setItem(MODE_KEY, "presets");
    localStorage.setItem(TARGET_KEY, "155");
    localStorage.setItem(SOURCE_KEY, "pl:1");   // start scoped to the playlist
    vi.mocked(api.get).mockResolvedValue({
      tracks: [{ path: "/x.mp3", title: "X", artist: "Ar", bpm: 155, starred: false, run_bpm: 155, rate: 1, from_playlist: true }],
      target: 155, count: 1, octave_fold: true, tolerance_pct: 4,
      prefer_starred: true, playlist: 1,
    });
    render(<Run />);
    // Build the queue for playlist 1; wait for the build to settle (the primary
    // action flips Start run → Rebuild queue once queueInfo is set).
    fireEvent.click(screen.getByLabelText("Start run"));
    await screen.findByLabelText("Rebuild queue");
    // No stale-source warning yet — source still matches the build.
    expect(screen.queryByText(/Source changed/i)).toBeNull();
    // Switch the source to the whole library → should prompt a Rebuild.
    fireEvent.change(screen.getByLabelText("Run source"), { target: { value: "library" } });
    expect(await screen.findByText(/Source changed to your whole library/i)).toBeTruthy();
  });
});
