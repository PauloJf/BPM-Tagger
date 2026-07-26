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
  // Ordered log of the player mutations startRun makes, plus the last value each
  // setter received — startRun's clear-then-re-set order is load-bearing.
  calls: [] as string[],
  lockSet: undefined as undefined | null | { target: number; octave: boolean; stretchLimitPct: number },
  sourceSet: "unset" as unknown,
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
    setTempoLock(lock: typeof h.lockSet) { h.calls.push("setTempoLock"); h.lockSet = lock; },
    playQueue() { h.calls.push("playQueue"); },
    setRunSource(id: unknown) { h.calls.push("setRunSource"); h.sourceSet = id; },
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
// Stand-in that surfaces the props Run passes, so the queue-panel wiring can be
// asserted without dragging the popover's own query/mutation plumbing in here.
vi.mock("../components/AddToPlaylistMenu", () => ({
  default: ({ paths, label }: { paths?: string[]; label?: string }) =>
    <button data-testid="save-queue" data-paths={JSON.stringify(paths ?? null)}>{label}</button>,
}));

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
  h.calls = [];
  h.lockSet = undefined;
  h.sourceSet = "unset";
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

  it("hard-caps the fill column height on the Queue tab (card scrolls, never the page)", () => {
    // min-height alone lets a long queue grow the column (and the page scroll);
    // the Queue tab must add run-queue-open, which turns the budget into a
    // definite height so the card shrinks and scrolls internally instead.
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    const { container } = render(<Run />);
    expect(container.querySelector(".run-mobile-fill")?.classList.contains("run-queue-open")).toBe(true);
  });

  it("drops the height cap on the other tabs (tiny screens may fall back to page scroll)", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    const { container } = render(<Run />);
    const fill = container.querySelector(".run-mobile-fill");
    expect(fill).toBeTruthy();
    expect(fill!.classList.contains("run-queue-open")).toBe(false);
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

describe("Run — the force-tempo toggle is gone (v2.10.0: max stretch is the only knob)", () => {
  // Guards against a partial revert: max stretch now filters at selection, so
  // there is no tolerance left for a force toggle to bypass. It must not come
  // back in any of its three old render sites (queue view, main page pre-run,
  // desktop controls column).
  it("does not render it in the Queue view", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", artist: "Artist", bpm: 120 };
    h.orderedQueue = [h.current];
    render(<Run />);
    expect(screen.queryByTestId("queue-force")).toBeNull();
    expect(screen.queryByText(/FORCE TEMPO/)).toBeNull();
  });

  it("does not render it pre-run on the main page", () => {
    localStorage.setItem(MODE_KEY, "presets");
    h.current = null;
    render(<Run />);
    expect(screen.queryByText(/FORCE TEMPO/)).toBeNull();
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

describe("Run — the queue header reports the limit the queue was built under", () => {
  it("shows 'built for N BPM · max ±X%' from the build response", async () => {
    localStorage.setItem(MODE_KEY, "queue");
    localStorage.setItem(TARGET_KEY, "155");
    vi.mocked(api.get).mockResolvedValue({
      tracks: [{ path: "/x.mp3", title: "X", artist: "Ar", bpm: 155, starred: false, run_bpm: 155, rate: 1 }],
      target: 155, count: 1, octave_fold: true, stretch_limit_pct: 15,
      prefer_starred: true, playlist: null,
    });
    render(<Run />);
    fireEvent.click(screen.getByLabelText("Start run"));
    expect(await screen.findByText(/built for 155 BPM · max ±15\.0%/)).toBeTruthy();
  });
});

describe("Run — starting a run sets the tempo lock and run source, after playQueue", () => {
  // playQueue now *exits run mode* (clears both) so Play elsewhere can't hijack
  // a run. startRun therefore clear-then-re-sets, relying on all three writes
  // landing in one batch. That ordering is invisible and easy to "tidy" away —
  // these assertions fail loudly if anyone moves the setters above playQueue.
  const build = () => {
    vi.mocked(api.get).mockResolvedValue({
      tracks: [{ path: "/x.mp3", title: "X", artist: "Ar", bpm: 155, starred: false,
                 run_bpm: 155, rate: 1, from_playlist: true }],
      target: 155, count: 1, octave_fold: true, stretch_limit_pct: 15,
      prefer_starred: true, playlist: 1,
    });
  };

  it("both are set, and both are re-set AFTER playQueue cleared them", async () => {
    localStorage.setItem(MODE_KEY, "presets");
    localStorage.setItem(TARGET_KEY, "155");
    localStorage.setItem(SOURCE_KEY, "pl:1");
    build();
    render(<Run />);
    fireEvent.click(screen.getByLabelText("Start run"));
    await screen.findByLabelText("Rebuild queue");

    // The run is locked to the target and scoped to the chosen playlist.
    expect(h.lockSet).toEqual({ target: 155, octave: true, stretchLimitPct: 15 });
    expect(h.sourceSet).toBe(1);
    // …and both writes come after the queue takeover, or playQueue would wipe them.
    const q = h.calls.lastIndexOf("playQueue");
    expect(q).toBeGreaterThanOrEqual(0);
    expect(h.calls.lastIndexOf("setRunSource")).toBeGreaterThan(q);
    expect(h.calls.lastIndexOf("setTempoLock")).toBeGreaterThan(q);
  });

  it("scopes a pooled run to 'mine' (still after playQueue)", async () => {
    localStorage.setItem(MODE_KEY, "presets");
    localStorage.setItem(TARGET_KEY, "155");
    localStorage.setItem(SOURCE_KEY, "mine");
    h.playlists = [
      { id: 1, name: "Alpha", source: "spotify", available: 5, total: 10, image_url: null },
      { id: 2, name: "Beta", source: "local", available: 3, total: 3, image_url: null },
    ];
    build();
    render(<Run />);
    fireEvent.click(screen.getByLabelText("Start run"));
    await screen.findByLabelText("Rebuild queue");

    expect(h.sourceSet).toBe("mine");
    expect(h.calls.lastIndexOf("setRunSource")).toBeGreaterThan(h.calls.lastIndexOf("playQueue"));
  });
});

describe("Run — changing the source mid-run prompts a Rebuild", () => {
  it("warns that the source changed after a build, until Rebuild", async () => {
    localStorage.setItem(MODE_KEY, "presets");
    localStorage.setItem(TARGET_KEY, "155");
    localStorage.setItem(SOURCE_KEY, "pl:1");   // start scoped to the playlist
    vi.mocked(api.get).mockResolvedValue({
      tracks: [{ path: "/x.mp3", title: "X", artist: "Ar", bpm: 155, starred: false, run_bpm: 155, rate: 1, from_playlist: true }],
      target: 155, count: 1, octave_fold: true, stretch_limit_pct: 15,
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

describe("Run — saving the queue as a playlist", () => {
  const showQueue = () => localStorage.setItem(MODE_KEY, "queue");

  it("offers Save with the queue's paths, in queue order", () => {
    showQueue();
    h.current = { path: "/music/a.mp3", title: "A" };
    h.orderedQueue = [
      { path: "/music/a.mp3", title: "A" },
      { path: "/music/b.mp3", title: "B" },
    ];
    render(<Run />);

    const save = screen.getByTestId("save-queue");
    expect(JSON.parse(save.getAttribute("data-paths")!))
      .toEqual(["/music/a.mp3", "/music/b.mp3"]);
  });

  it("hides Save from a player — playlist management is admin-only", () => {
    showQueue();
    h.role = "player";
    h.current = { path: "/music/a.mp3", title: "A" };
    h.orderedQueue = [{ path: "/music/a.mp3", title: "A" }];
    render(<Run />);
    expect(screen.queryByTestId("save-queue")).toBeNull();
  });

  it("hides Save when there is no queue to save", () => {
    showQueue();
    h.orderedQueue = [];
    render(<Run />);
    expect(screen.queryByTestId("save-queue")).toBeNull();
  });
});

describe("Run — ?bpm= deep link from the Cadence page", () => {
  const withSearch = (search: string) => {
    window.history.replaceState(null, "", `/run${search}`);
  };

  it("adopts the target from the URL over the remembered one", () => {
    localStorage.setItem(TARGET_KEY, "120");
    withSearch("?bpm=175");
    render(<Run />);
    expect(screen.getByTestId("run-target").textContent).toBe("175");
  });

  it("strips the param and persists the target, so a refresh keeps it", () => {
    localStorage.setItem(TARGET_KEY, "120");
    withSearch("?bpm=175");
    render(<Run />);
    expect(window.location.search).toBe("");
    expect(localStorage.getItem(TARGET_KEY)).toBe("175");
  });

  it("ignores an out-of-range param and keeps the remembered target", () => {
    localStorage.setItem(TARGET_KEY, "120");
    withSearch("?bpm=9999");
    render(<Run />);
    expect(screen.getByTestId("run-target").textContent).toBe("120");
  });
});
