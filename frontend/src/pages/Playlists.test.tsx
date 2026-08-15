import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { Playlist } from "../lib/types";

// Playlists pulls in router/query/grabber context; mock them so the card grid
// renders in isolation and its PATCH calls can be asserted on.
const h = vi.hoisted(() => ({
  playlists: [] as unknown[],
  grabber: { enabled: false, spotify: { connected: false } } as Record<string, unknown>,
  readiness: undefined as unknown,
  navigated: [] as string[],
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
  useNavigate: () => (to: string) => { h.navigated.push(to); },
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: () => {} }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === "playlists")
      return { data: { playlists: h.playlists }, isLoading: false, isError: false };
    if (queryKey[0] === "run-readiness")
      return { data: h.readiness, isLoading: false, isError: false };
    return { data: undefined, isLoading: false, isError: false };
  },
  useMutation: (opts: { mutationFn: (v: unknown) => unknown }) => ({
    mutate: (v: unknown) => opts.mutationFn(v),
    isPending: false,
    variables: undefined,
  }),
}));

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn(), patch: vi.fn(() => Promise.resolve({})) },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../hooks/useGrabberStatus", () => ({ useGrabberStatus: () => ({ data: h.grabber }) }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));

import Playlists, { mergeSummary } from "./Playlists";
import { api } from "../lib/api";

function pl(over: Partial<Playlist> = {}): Playlist {
  return {
    id: 1, source: "local", spotify_id: null, navidrome_id: null, name: "Mix",
    description: "", pinned: 0, snapshot_id: null, enabled: 1, image_url: null,
    track_count: 0, last_synced_at: null, have_count: 0, missing_count: 0,
    queued_count: 0, new_count: 0, removed_count: 0, indexed_count: 0, ...over,
  };
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.mocked(api.patch).mockClear();
  h.playlists = [];
  h.grabber = { enabled: false, spotify: { connected: false } };
  h.readiness = undefined;
  h.navigated = [];
});

const READINESS = {
  presets: [{ name: "Easy", bpm: 155 }, { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 }],
  stretch_limit_pct: 15,
  library: { "155": 78, "165": 34, "175": 0 },
  playlists: [{ id: 7, name: "Long Runs", counts: { "155": 11, "165": 8, "175": 0 } }],
};

describe("Playlists — pinning", () => {
  it("pins an unpinned playlist through PATCH", () => {
    h.playlists = [pl({ id: 7, name: "Long Runs" })];
    render(<Playlists />);

    const toggle = screen.getByRole("button", { name: /^Pin "Long Runs"$/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);

    expect(api.patch).toHaveBeenCalledWith("/api/playlists/7", { pinned: true });
  });

  it("unpins a pinned one, and marks the card", () => {
    h.playlists = [pl({ id: 7, name: "Long Runs", pinned: 1 })];
    render(<Playlists />);

    const toggle = screen.getByRole("button", { name: /^Unpin "Long Runs"$/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(toggle);

    expect(api.patch).toHaveBeenCalledWith("/api/playlists/7", { pinned: false });
  });

  it("renders the server's order as-is — pinned-first is decided server-side", () => {
    // Sorting client-side by name would undo the ORDER BY pinned DESC, name.
    h.playlists = [
      pl({ id: 3, name: "Zed", pinned: 1 }),
      pl({ id: 1, name: "Alpha" }),
      pl({ id: 2, name: "Beta" }),
    ];
    render(<Playlists />);
    const names = screen.getAllByRole("link")
      .map((a) => a.textContent || "")
      .filter((txt) => /Zed|Alpha|Beta/.test(txt));
    expect(names[0]).toMatch(/Zed/);
    expect(names[1]).toMatch(/Alpha/);
    expect(names[2]).toMatch(/Beta/);
  });

  it("shows a description on the card when there is one", () => {
    h.playlists = [pl({ description: "Threshold intervals" })];
    render(<Playlists />);
    expect(screen.getByText("Threshold intervals")).toBeTruthy();
  });
});

describe("Playlists — card artwork", () => {
  const srcs = (c: HTMLElement) => Array.from(c.querySelectorAll("img")).map((i) => i.getAttribute("src"));

  it("points a local card at the cover endpoint", () => {
    // Local playlists have no image_url — the endpoint serves a custom cover or
    // an auto-collage, and 404s into the ♪ placeholder when there's neither.
    h.playlists = [pl({ id: 4, source: "local", image_url: null })];
    const { container } = render(<Playlists />);
    expect(srcs(container)).toContain("/api/playlists/4/cover");
  });

  it("leaves a synced card on its source's image_url", () => {
    h.playlists = [pl({ id: 4, source: "spotify", image_url: "https://i.scdn.co/image/pl" })];
    const { container } = render(<Playlists />);
    expect(srcs(container)).toEqual(["https://i.scdn.co/image/pl"]);
  });
});

describe("Playlists — cadence strip", () => {
  it("renders one card per preset with the library-wide count", () => {
    h.readiness = READINESS;
    const { container } = render(<Playlists />);

    const cards = Array.from(container.querySelectorAll(".cadence-card"));
    expect(cards).toHaveLength(3);
    expect(cards[0].textContent).toContain("Easy");
    expect(cards[0].textContent).toContain("155");
    expect(cards[0].textContent).toContain("78 ready");
    expect(cards[0].getAttribute("href")).toBe("/cadence?bpm=155");
  });

  it("renders nothing at all while readiness is loading — no layout jump", () => {
    const { container } = render(<Playlists />);
    expect(container.querySelector(".cadence-strip")).toBeNull();
  });
});

describe("Playlists — per-preset readiness badges", () => {
  it("shows a badge per preset the playlist has tracks for, and skips the zeroes", () => {
    h.readiness = READINESS;
    h.playlists = [pl({ id: 7, name: "Long Runs" })];
    render(<Playlists />);

    expect(screen.getByText("155:11")).toBeTruthy();
    expect(screen.getByText("165:8")).toBeTruthy();
    expect(screen.queryByText(/^175:/)).toBeNull();   // zero-count preset omitted
  });

  it("preselects the playlist as the run source and deep-links the target", () => {
    h.readiness = READINESS;
    h.playlists = [pl({ id: 7, name: "Long Runs" })];
    render(<Playlists />);

    fireEvent.click(screen.getByText("155:11"));
    expect(localStorage.getItem("bpm.run.source")).toBe("pl:7");
    expect(h.navigated).toEqual(["/run?bpm=155"]);
  });

  it("uses buttons, not links — the whole card is already an anchor", () => {
    // An <a> inside an <a> is invalid HTML; browsers un-nest it and the inner
    // link stops behaving like one.
    h.readiness = READINESS;
    h.playlists = [pl({ id: 7, name: "Long Runs" })];
    render(<Playlists />);
    expect(screen.getByText("155:11").tagName).toBe("BUTTON");
  });

  it("renders no badge row for a playlist with nothing runnable", () => {
    h.readiness = { ...READINESS, playlists: [{ id: 7, name: "Long Runs", counts: { "155": 0, "165": 0, "175": 0 } }] };
    h.playlists = [pl({ id: 7, name: "Long Runs" })];
    render(<Playlists />);
    expect(screen.queryByText(/^155:/)).toBeNull();
  });
});

describe("Playlists — merge reporting", () => {
  const c = (over: Partial<Parameters<typeof mergeSummary>[0]> = {}) => ({
    added: 0, already_present: 0, skipped_duplicate: 0, not_in_library: 0, ...over,
  });

  it("reads as one number when the merge was clean", () => {
    expect(mergeSummary(c({ added: 12 }))).toBe("Added 12");
  });

  it("names each way a track failed to be added, in the same words as the copy flow", () => {
    expect(mergeSummary(c({ added: 3, already_present: 2, skipped_duplicate: 1, not_in_library: 4 })))
      .toBe("Added 3 · 2 already there · 1 duplicate · 4 not in library");
  });

  it("pluralizes the cross-source duplicate skip", () => {
    expect(mergeSummary(c({ skipped_duplicate: 2 }))).toBe("Added 0 · 2 duplicates");
  });
});
