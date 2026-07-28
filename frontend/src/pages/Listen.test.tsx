import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Listen is a thin view over the player context; mock the context/hooks so the
// page renders in isolation and tests drive role, access, viewport, the
// current track and the playlist list via this hoisted, mutable holder.
const h = vi.hoisted(() => ({
  role: "admin" as string | null,
  fullAccess: true,
  listenMode: "off" as string,
  mobile: false,
  current: null as null | { path: string; title: string; artist?: string; bpm?: number | null; starred?: boolean; ephemeral?: boolean },
  orderedQueue: [] as Array<{ path: string; title: string }>,
  tempoLock: null as null | { target: number; octave: boolean; stretchLimitPct: number },
  playing: false,
  online: true,
  buffering: false,
  radio: false,
  listenSource: null as number | "mine" | "library" | null,
  playlists: [] as Array<{ id: number; name: string; source: string; available: number; total: number; image_url: string | null }>,
  // Ordered log of the player mutations startPlayback makes — the
  // playQueue-then-setListenSource order is load-bearing (mirrors Run).
  calls: [] as string[],
  queued: null as null | unknown[],
  shuffleOpt: undefined as undefined | boolean,
  sourceSet: "unset" as unknown,
  radioSet: null as null | boolean,
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const k = queryKey[0];
    if (k === "run-playlists") return { data: { playlists: h.playlists }, isLoading: false };
    if (k === "track-bpm") return { data: { track: { bpm: 120, starred: 0 }, quality: null } };
    return { data: undefined };
  },
}));

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({})), post: vi.fn(() => Promise.resolve({})) },
  audioUrl: (p: string) => `/audio?path=${p}`,
}));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role, fullAccess: h.fullAccess, listenMode: h.listenMode }) }));
vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    current: h.current, playing: h.playing, audioRef: { current: null },
    error: null, buffering: h.buffering, bufferedPct: 0, online: h.online,
    orderedQueue: h.orderedQueue, orderPos: 0, tempoLock: h.tempoLock,
    shuffle: false, repeat: "off", volume: 1, setVolume() {},
    radio: h.radio, setRadio(on: boolean) { h.radioSet = on; },
    listenSource: h.listenSource,
    setListenSource(id: unknown) { h.calls.push("setListenSource"); h.sourceSet = id; },
    playQueue(tracks: unknown[], _start: number, opts?: { shuffle?: boolean }) {
      h.calls.push("playQueue"); h.queued = tracks; h.shuffleOpt = opts?.shuffle;
    },
    setTrackStarred() {}, toggleShuffle() {}, cycleRepeat() {},
    next() {}, prev() {}, toggle() {}, stop() {},
  }),
}));
vi.mock("../hooks/useWaveform", () => ({ useWaveform: () => ({ loading: false }) }));
vi.mock("../hooks/useAudioTime", () => ({ useAudioTime: () => ({ time: 0, dur: 0 }), fmtTime: () => "0:00" }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../hooks/useCoverGlow", () => ({ useCoverGlow: () => ({ background: "transparent", opacity: 0 }) }));
vi.mock("../hooks/useIsMobile", () => ({ useIsMobile: () => h.mobile }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/LyricsPanel", () => ({ LyricsPanel: () => null }));
vi.mock("../components/BpmDisplay", () => ({ BpmDisplay: () => null }));
vi.mock("../components/PlayerCover", () => ({ default: () => <div data-testid="cover" /> }));
vi.mock("../components/QueueList", () => ({ default: () => <div data-testid="queue-list" /> }));

// Import after the mocks are registered.
import Listen from "./Listen";
import { api } from "../lib/api";

const SOURCE_KEY = "bpm.listen.source";

beforeEach(() => {
  cleanup();
  localStorage.clear();
  h.role = "admin";
  h.fullAccess = true;
  h.listenMode = "off";
  h.mobile = false;
  h.current = null;
  h.orderedQueue = [];
  h.tempoLock = null;
  h.playing = false;
  h.online = true;
  h.buffering = false;
  h.radio = false;
  h.listenSource = null;
  h.calls = [];
  h.queued = null;
  h.shuffleOpt = undefined;
  h.sourceSet = "unset";
  h.radioSet = null;
  h.playlists = [{ id: 1, name: "Alpha", source: "local", available: 5, total: 10, image_url: null }];
  vi.mocked(api.get).mockResolvedValue({});
});

const sourceSelect = () => screen.getByLabelText("Source to play") as HTMLSelectElement;
const options = () => Array.from(sourceSelect().options).map((o) => o.textContent);

describe("Listen — source picker access rules (mirrors Run's)", () => {
  it("full access defaults to the whole library, with playlists offered too", () => {
    render(<Listen />);
    expect(sourceSelect().value).toBe("library");
    expect(options()).toEqual(["Whole library", "Alpha (10)"]);
  });

  it("a scoped player never sees 'Whole library'", () => {
    h.role = "player";
    h.fullAccess = false;
    render(<Listen />);
    expect(options()).not.toContain("Whole library");
    expect(sourceSelect().value).toBe("pl:1");
  });

  it("offers 'All my music' to a scoped player with 2+ playlists and defaults to it", () => {
    h.role = "player";
    h.fullAccess = false;
    h.playlists = [
      { id: 1, name: "Alpha", source: "local", available: 5, total: 10, image_url: null },
      { id: 2, name: "Beta", source: "local", available: 3, total: 3, image_url: null },
    ];
    render(<Listen />);
    expect(options()).toContain("All my music");
    expect(sourceSelect().value).toBe("mine");
  });

  it("the shared full-access Guest (player role) still gets the library", () => {
    h.role = "player";
    h.fullAccess = true;
    render(<Listen />);
    expect(options()).toContain("Whole library");
    expect(sourceSelect().value).toBe("library");
  });

  it("falls back when the remembered playlist no longer exists", () => {
    localStorage.setItem(SOURCE_KEY, "pl:99");
    render(<Listen />);
    expect(sourceSelect().value).toBe("library");
  });
});

describe("Listen — starting playback", () => {
  const resp = {
    tracks: [
      { path: "/a.mp3", title: "A", artist: "Ar", bpm: 120, starred: true, disliked: false, duration_ms: 1000, loudness_lufs: -10 },
      { path: "/b.mp3", title: "B", artist: "Ar", bpm: null, starred: false, disliked: false, duration_ms: 1000, loudness_lufs: null },
    ],
    playlist: 1, count: 2,
  };

  it("fetches the library source and scopes the radio refill to it", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...resp, playlist: "library" });
    render(<Listen />);
    fireEvent.click(screen.getByText("Play"));
    await vi.waitFor(() => expect(h.calls).toContain("setListenSource"));
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/listen/queue?playlist=library");
    expect(h.sourceSet).toBe("library");
  });

  it("fetches a playlist source in order — un-analyzed tracks included", async () => {
    localStorage.setItem(SOURCE_KEY, "pl:1");
    vi.mocked(api.get).mockResolvedValue(resp);
    render(<Listen />);
    fireEvent.click(screen.getByText("Play"));
    await vi.waitFor(() => expect(h.calls).toContain("playQueue"));
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/listen/queue?playlist=1");
    expect(h.shuffleOpt).toBe(false);
    expect((h.queued as Array<{ path: string }>).map((t) => t.path)).toEqual(["/a.mp3", "/b.mp3"]);
  });

  it("Shuffle plays the same queue shuffled", async () => {
    vi.mocked(api.get).mockResolvedValue(resp);
    render(<Listen />);
    fireEvent.click(screen.getByText("Shuffle"));
    await vi.waitFor(() => expect(h.calls).toContain("playQueue"));
    expect(h.shuffleOpt).toBe(true);
  });

  it("sets the listen source AFTER playQueue (which clears it) — order is load-bearing", async () => {
    localStorage.setItem(SOURCE_KEY, "pl:1");
    vi.mocked(api.get).mockResolvedValue(resp);
    render(<Listen />);
    fireEvent.click(screen.getByText("Play"));
    await vi.waitFor(() => expect(h.calls).toContain("setListenSource"));
    expect(h.sourceSet).toBe(1);
    expect(h.calls.lastIndexOf("setListenSource")).toBeGreaterThan(h.calls.lastIndexOf("playQueue"));
  });

  it("surfaces an empty source instead of playing nothing", async () => {
    localStorage.setItem(SOURCE_KEY, "pl:1");
    vi.mocked(api.get).mockResolvedValue({ tracks: [], playlist: 1, count: 0 });
    render(<Listen />);
    fireEvent.click(screen.getByText("Play"));
    expect(await screen.findByText(/No playable tracks in “Alpha”/)).toBeTruthy();
    expect(h.calls).not.toContain("playQueue");
  });
});

describe("Listen — radio toggle", () => {
  it("toggles radio through the player context", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.listenSource = 1;
    render(<Listen />);
    fireEvent.click(screen.getByTestId("radio-toggle"));
    expect(h.radioSet).toBe(true);
  });
});

describe("Listen — tempo-lock awareness", () => {
  it("shows a chip linking to Run while a tempo lock is active", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.tempoLock = { target: 165, octave: true, stretchLimitPct: 15 };
    render(<Listen />);
    const chip = screen.getByText(/Tempo locked to 165 BPM/);
    expect(chip.closest("a")?.getAttribute("href")).toBe("/run");
  });

  it("drops the Run link for a jukebox-only kiosk (no /run route exists)", () => {
    h.role = "player";
    h.fullAccess = false;
    h.listenMode = "only";
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    h.tempoLock = { target: 165, octave: true, stretchLimitPct: 15 };
    render(<Listen />);
    expect(screen.getByText(/Tempo locked to 165 BPM/).closest("a")).toBeNull();
  });

  it("shows no chip at native tempo", () => {
    h.current = { path: "/a.mp3", title: "A", bpm: 120 };
    render(<Listen />);
    expect(screen.queryByText(/Tempo locked/)).toBeNull();
  });
});

describe("Listen — kiosk-safe links", () => {
  it("does not link the title/artist to admin pages for a player", () => {
    h.role = "player";
    h.fullAccess = false;
    h.listenMode = "on";
    h.current = { path: "/a.mp3", title: "SongTitle", artist: "SomeArtist", bpm: 120 };
    render(<Listen />);
    expect(screen.getByText("SongTitle").closest("a")).toBeNull();
    expect(screen.getByText("SomeArtist").closest("a")).toBeNull();
  });

  it("links them for the admin", () => {
    h.current = { path: "/a.mp3", title: "SongTitle", artist: "SomeArtist", bpm: 120 };
    render(<Listen />);
    expect(screen.getByText("SongTitle").closest("a")).toBeTruthy();
    expect(screen.getByText("SomeArtist").closest("a")).toBeTruthy();
  });
});

describe("Listen — empty state", () => {
  it("tells a scoped player with no playlists what's going on", () => {
    h.role = "player";
    h.fullAccess = false;
    h.playlists = [];
    render(<Listen />);
    expect(screen.getByText(/No playlists have been shared with this account/)).toBeTruthy();
  });
});

describe("Listen — desktop queue panel", () => {
  it("renders the shared queue list once more than one track is queued", () => {
    h.current = { path: "/a.mp3", title: "A" };
    h.orderedQueue = [{ path: "/a.mp3", title: "A" }, { path: "/b.mp3", title: "B" }];
    render(<Listen />);
    expect(screen.getByTestId("queue-list")).toBeTruthy();
    expect(screen.getByText("Queue · 2")).toBeTruthy();
  });

  it("hides it for a single-track queue", () => {
    h.current = { path: "/a.mp3", title: "A" };
    h.orderedQueue = [{ path: "/a.mp3", title: "A" }];
    render(<Listen />);
    expect(screen.queryByTestId("queue-list")).toBeNull();
  });
});

describe("Listen — mobile one-screen layout (bottom tabs)", () => {
  beforeEach(() => { h.mobile = true; });

  const track = () => {
    h.current = { path: "/a.mp3", title: "A", artist: "Ar", bpm: 120 };
    h.orderedQueue = [{ path: "/a.mp3", title: "A" }, { path: "/b.mp3", title: "B" }];
  };

  it("uses the fill column with the cover in the flexible slot", () => {
    track();
    const { container } = render(<Listen />);
    expect(container.querySelector(".run-mobile-fill")).toBeTruthy();
    expect(screen.getByTestId("cover-slot")).toBeTruthy();
    // No page-growing queue card in the Playing view.
    expect(screen.queryByTestId("queue-list")).toBeNull();
  });

  it("shows the bottom Playing/Queue switcher only once something is loaded", () => {
    render(<Listen />);
    expect(screen.queryByTestId("listen-tabs")).toBeNull();
    cleanup();
    track();
    render(<Listen />);
    expect(screen.getByTestId("listen-tabs")).toBeTruthy();
  });

  it("the Queue tab swaps the cover for the queue and hard-caps the column", () => {
    track();
    const { container } = render(<Listen />);
    fireEvent.click(screen.getByText(/^Queue · 2$/));
    expect(screen.getByTestId("queue-list")).toBeTruthy();
    expect(screen.queryByTestId("cover-slot")).toBeNull();
    // Hard height cap so the queue scrolls inside its card, not the page.
    expect(container.querySelector(".run-mobile-fill")?.classList.contains("run-queue-open")).toBe(true);
  });

  it("hosts the source picker in the Queue tab mid-playback", () => {
    track();
    render(<Listen />);
    // Playing view: no picker (it would steal cover height).
    expect(screen.queryByLabelText("Source to play")).toBeNull();
    fireEvent.click(screen.getByText(/^Queue · 2$/));
    expect(screen.getByTestId("queue-source")).toBeTruthy();
    expect(screen.getByLabelText("Source to play")).toBeTruthy();
  });

  it("keeps the transport reachable from the Queue tab", () => {
    track();
    render(<Listen />);
    fireEvent.click(screen.getByText(/^Queue · 2$/));
    expect(screen.getByLabelText("Play")).toBeTruthy();
    expect(screen.getByLabelText("Next")).toBeTruthy();
  });

  it("drops the volume slider on mobile (hardware buttons own it)", () => {
    track();
    render(<Listen />);
    expect(screen.queryByLabelText("Volume")).toBeNull();
    h.mobile = false;
    cleanup();
    render(<Listen />);
    expect(screen.getByLabelText("Volume")).toBeTruthy();
  });

  it("shows the picker inline pre-playback (no tabs yet)", () => {
    render(<Listen />);
    expect(screen.getByLabelText("Source to play")).toBeTruthy();
    expect(screen.queryByTestId("listen-tabs")).toBeNull();
  });
});
