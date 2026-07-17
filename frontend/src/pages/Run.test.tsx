import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

// Run pulls in a lot of context/hooks; mock them so the page renders in
// isolation and we can drive `role`, `current`, the queue, and the playlist
// list per test via this hoisted, mutable holder.
const h = vi.hoisted(() => ({
  role: "admin" as string | null,
  current: null as null | { path: string; title: string; artist?: string; bpm?: number | null },
  orderedQueue: [] as Array<{ path: string; title: string; artist?: string; bpm?: number | null; fromPlaylist?: boolean }>,
  playlists: [] as Array<{ id: number; name: string; source: string; available: number; total: number; image_url: string | null }>,
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

vi.mock("../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() }, audioUrl: (p: string) => `/audio?path=${p}` }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role }) }));
vi.mock("../lib/player", () => ({
  lockRate: () => 1,
  usePlayer: () => ({
    current: h.current, playing: false, audioRef: { current: null },
    orderedQueue: h.orderedQueue, orderPos: 0, tempoLock: null, runSource: null,
    updateTrackBpm() {}, setTempoLock() {}, playQueue() {}, setRunSource() {},
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

const MODE_KEY = "bpm.run.mode";

beforeEach(() => {
  cleanup();
  localStorage.clear();
  h.role = "admin";
  h.current = null;
  h.orderedQueue = [];
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
