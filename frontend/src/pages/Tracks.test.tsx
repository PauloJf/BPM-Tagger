import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { Track, TracksPage } from "../lib/types";

// Tracks pulls in a lot of page chrome (queue actions, artwork, tabs) that's
// irrelevant to the decode-warnings surfacing under test; mock it all away so
// the table and filter pills render in isolation.
const h = vi.hoisted(() => ({
  data: undefined as TracksPage | undefined,
  params: new URLSearchParams(),
  setParams: vi.fn(),
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
  useSearchParams: () => [h.params, h.setParams],
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === "tracks") return { data: h.data, isLoading: false };
    return { data: undefined };
  },
  useQueryClient: () => ({ invalidateQueries: () => {}, setQueryData: () => {} }),
}));

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({})), post: vi.fn(() => Promise.resolve({})) },
}));

vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    play: () => {}, playNext: () => {}, enqueue: () => {}, isQueued: () => false,
    orderedQueue: [], removeAt: () => {},
  }),
}));

vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/LibraryTabs", () => ({ default: () => null }));
vi.mock("../components/QueueActions", () => ({ QueueActions: () => null }));
vi.mock("../components/AddToPlaylistMenu", () => ({ default: () => null }));
vi.mock("../components/Artwork", () => ({
  useArtwork: () => [false, () => {}],
  ArtToggle: () => null,
  Cover: () => null,
}));

// Import after the mocks are registered.
import Tracks from "./Tracks";

function track(overrides: Partial<Track>): Track {
  return {
    id: 1, file_path: "/music/a.mp3", file_hash: null, bpm: 120, bpm_dr: null, bpm_es: null,
    bpm_lb: null, bpm_confidence: 0.9, detector: "librosa", analyzed_at: null, status: "done",
    error_message: null, needs_review: 0, reviewed: 0, locked: 0,
    ...overrides,
  };
}

function page(tracks: Track[], extra: Partial<TracksPage> = {}): TracksPage {
  return {
    tracks, total: tracks.length, page: 1, pages: 1, per_page: 50, filter: "", sort: "",
    all_count: tracks.length, review_count: 0, locked_count: 0, deleted_count: 0,
    no_isrc_count: 0, starred_count: 0, disliked_count: 0, unplaylisted_count: 0,
    problems_count: 0,
    ...extra,
  };
}

beforeEach(() => {
  cleanup();
  h.params = new URLSearchParams();
  h.setParams.mockReset();
  h.data = undefined;
});

describe("Tracks — Problems filter pill", () => {
  it("renders after Locked with the server-reported count", () => {
    h.data = page([], { problems_count: 3 });
    render(<Tracks />);
    const buttons = screen.getAllByRole("button").map((b) => b.textContent || "");
    const lockedIdx = buttons.findIndex((t) => t.startsWith("Locked"));
    const problemsIdx = buttons.findIndex((t) => t.startsWith("Problems"));
    expect(lockedIdx).toBeGreaterThanOrEqual(0);
    expect(problemsIdx).toBe(lockedIdx + 1);
    expect(screen.getByRole("button", { name: "Problems 3" })).toBeTruthy();
  });

  it("sets filter=problems in the URL params when clicked", () => {
    h.data = page([], { problems_count: 2 });
    render(<Tracks />);
    fireEvent.click(screen.getByRole("button", { name: "Problems 2" }));
    expect(h.setParams).toHaveBeenCalled();
    const updater = h.setParams.mock.calls[0][0] as (p: URLSearchParams) => URLSearchParams;
    const next = updater(new URLSearchParams());
    expect(next.get("filter")).toBe("problems");
  });
});

describe("Tracks — decode warning row badge", () => {
  it("shows the badge with the joined detail text on hover for a flagged row", () => {
    h.data = page([
      track({ file_path: "/music/bad.mp3", decode_warnings: [
        { code: "empty_windows", detail: "2 of 3 analysis windows decoded to nothing" },
      ] }),
    ]);
    render(<Tracks />);
    const badge = screen.getByText("decode");
    expect(badge.closest(".badge")?.getAttribute("title")).toBe("2 of 3 analysis windows decoded to nothing");
  });

  it("shows no decode badge for a clean row", () => {
    h.data = page([track({ file_path: "/music/clean.mp3", decode_warnings: [] })]);
    render(<Tracks />);
    expect(screen.queryByText("decode")).toBeNull();
  });
});
