import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Listen is a thin view over the player context; mock the context/hooks so the
// page renders in isolation and tests drive role, listenMode, the current
// track and the playlist list via this hoisted, mutable holder.
const h = vi.hoisted(() => ({
  role: "admin" as string | null,
  listenMode: "off" as string,
  current: null as null | { path: string; title: string; artist?: string; bpm?: number | null; starred?: boolean; ephemeral?: boolean },
  orderedQueue: [] as Array<{ path: string; title: string }>,
  tempoLock: null as null | { target: number; octave: boolean; stretchLimitPct: number },
  playing: false,
  online: true,
  buffering: false,
  radio: false,
  listenSource: null as number | "mine" | null,
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
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role, listenMode: h.listenMode }) }));
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
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/LyricsPanel", () => ({ LyricsPanel: () => null }));
vi.mock("../components/BpmDisplay", () => ({ BpmDisplay: () => null }));
vi.mock("../components/Artwork", () => ({ Cover: () => <div data-testid="cover" /> }));
vi.mock("../components/QueueList", () => ({ default: () => <div data-testid="queue-list" /> }));

// Import after the mocks are registered.
import Listen from "./Listen";
import { api } from "../lib/api";

const SOURCE_KEY = "bpm.listen.source";

beforeEach(() => {
  cleanup();
  localStorage.clear();
  h.role = "admin";
  h.listenMode = "off";
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

describe("Listen — source picker", () => {
  it("lists playlists and defaults to the first when nothing is remembered", () => {
    render(<Listen />);
    const select = screen.getByLabelText("Playlist to play") as HTMLSelectElement;
    expect(select.value).toBe("pl:1");
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Alpha (10)"]);
  });

  it("offers 'All my music' to a player with 2+ playlists and defaults to it", () => {
    h.role = "player";
    h.playlists = [
      { id: 1, name: "Alpha", source: "local", available: 5, total: 10, image_url: null },
      { id: 2, name: "Beta", source: "local", available: 3, total: 3, image_url: null },
    ];
    render(<Listen />);
    const select = screen.getByLabelText("Playlist to play") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toContain("All my music");
    expect(select.value).toBe("mine");
  });

  it("never offers the pooled source to an admin", () => {
    h.playlists = [
      { id: 1, name: "Alpha", source: "local", available: 5, total: 10, image_url: null },
      { id: 2, name: "Beta", source: "local", available: 3, total: 3, image_url: null },
    ];
    render(<Listen />);
    const select = screen.getByLabelText("Playlist to play") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).not.toContain("All my music");
  });

  it("falls back when the remembered playlist no longer exists", () => {
    localStorage.setItem(SOURCE_KEY, "pl:99");
    render(<Listen />);
    expect((screen.getByLabelText("Playlist to play") as HTMLSelectElement).value).toBe("pl:1");
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

  it("fetches the listen queue and plays it in order", async () => {
    vi.mocked(api.get).mockResolvedValue(resp);
    render(<Listen />);
    fireEvent.click(screen.getByText("Play"));
    await vi.waitFor(() => expect(h.calls).toContain("playQueue"));
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/listen/queue?playlist=1");
    expect(h.shuffleOpt).toBe(false);
    // Un-analyzed tracks (bpm null) are queued too — this is the regular player.
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
    vi.mocked(api.get).mockResolvedValue(resp);
    render(<Listen />);
    fireEvent.click(screen.getByText("Play"));
    await vi.waitFor(() => expect(h.calls).toContain("setListenSource"));
    expect(h.sourceSet).toBe(1);
    expect(h.calls.lastIndexOf("setListenSource")).toBeGreaterThan(h.calls.lastIndexOf("playQueue"));
  });

  it("surfaces an empty playlist instead of playing nothing", async () => {
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
  it("tells a player with no playlists what's going on", () => {
    h.role = "player";
    h.playlists = [];
    render(<Listen />);
    expect(screen.getByText(/No playlists have been shared with this account/)).toBeTruthy();
  });

  it("points the admin at the Playlists page", () => {
    h.playlists = [];
    render(<Listen />);
    expect(screen.getByText(/create one on the Playlists page/)).toBeTruthy();
  });
});

describe("Listen — queue panel", () => {
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
