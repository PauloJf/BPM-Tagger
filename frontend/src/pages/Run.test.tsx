import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Run pulls in a lot of context/hooks; mock them so the page renders in
// isolation and we can drive `role`, `current`, the queue, and the playlist
// list per test via this hoisted, mutable holder.
const h = vi.hoisted(() => ({
  role: "admin" as string | null,
  current: null as null | { path: string; title: string; artist?: string; bpm?: number | null },
  orderedQueue: [] as Array<{ path: string; title: string; artist?: string; bpm?: number | null; starred?: boolean; fromPlaylist?: boolean }>,
  tempoLock: null as null | { target: number; octave: boolean; stretchLimitPct: number },
  playing: false,
  runSource: null as number | null,
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
    if (k === "track-bpm") return { data: { track: { bpm: 120, locked: 0 }, quality: null } };
    return { data: undefined };
  },
}));

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({})), post: vi.fn(() => Promise.resolve({})) },
  audioUrl: (p: string) => `/audio?path=${p}`,
}));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role }) }));
vi.mock("../lib/player", () => ({
  lockRate: () => 1,
  usePlayer: () => ({
    current: h.current, playing: h.playing, audioRef: { current: null },
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
import Run from "./Run";
import { api } from "../lib/api";

const MODE_KEY = "bpm.run.mode";
const TARGET_KEY = "bpm.run.target";
const SOURCE_KEY = "bpm.run.source";

beforeEach(() => {
  cleanup();
  localStorage.clear();
  h.role = "admin";
  h.current = null;
  h.orderedQueue = [];
  h.tempoLock = null;
  h.playing = false;
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

describe("Run — mobile queue height is bounded", () => {
  it("caps the queue scroll area so switching to Queue doesn't overflow the screen", () => {
    localStorage.setItem(MODE_KEY, "queue");
    h.current = { path: "/music/a.mp3", title: "A", bpm: 120 };
    h.orderedQueue = Array.from({ length: 40 }, (_, i) => ({ path: `/music/${i}.mp3`, title: `T${i}`, bpm: 120 }));
    render(<Run />);
    // Read the raw inline style so the assertion doesn't depend on jsdom's CSS
    // value parsing (clamp/calc/dvh).
    const style = screen.getByTestId("queue-list").getAttribute("style") || "";
    expect(style).toContain("100dvh");
    expect(style).toContain("560px");   // chrome reserve (admin: bannerReserve 0)
    expect(style).toContain("340px");   // hard cap
    // Regression guard against the previous too-tall values.
    expect(style).not.toContain("420px");
    expect(style).not.toContain("500px");
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
