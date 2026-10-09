import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";

// Search pulls in router/query/grabber context; mock them so the page renders in
// isolation and the artist entry points (Deezer artist list + per-row credits)
// can be driven directly. The modal is stubbed to a marker naming its artist.
const h = vi.hoisted(() => ({
  grabber: { enabled: true, spotify: { connected: true } } as Record<string, unknown>,
  results: [] as unknown[],
  artists: [] as unknown[],
  resolve: {} as Record<string, unknown>,
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({
    invalidateQueries: () => {},
    fetchQuery: (o: { queryFn: () => unknown }) => o.queryFn(),
  }),
  // Answer each query by key, as the page sees it once a search has run.
  useQuery: (o: { queryKey: unknown[]; enabled?: boolean }) => {
    if (!o.enabled) return { data: undefined, isFetching: false };
    if (o.queryKey[0] === "spotify-search") return { data: { results: h.results }, isFetching: false };
    if (o.queryKey[0] === "deezer-search-artists") return { data: { artists: h.artists }, isFetching: false };
    return { data: undefined, isFetching: false };
  },
  useMutation: () => ({ mutate: () => {}, isPending: false }),
}));

vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn((url: string) => {
      const name = new URL(url, "http://x").searchParams.get("name") || "";
      return Promise.resolve({ artist: h.resolve[name] ?? null });
    }),
    post: vi.fn(() => Promise.resolve({})),
  },
  ApiError: class extends Error {},
}));
vi.mock("../hooks/useGrabberStatus", () => ({ useGrabberStatus: () => ({ data: h.grabber }) }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/GrabberGate", () => ({ default: ({ children }: { children: unknown }) => <>{children as never}</> }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/ArtistModal", () => ({
  default: ({ dzId, name }: { dzId: string; name: string }) => <div data-testid="artist-modal">{name}:{dzId}</div>,
}));

import Search from "./Search";

function runSearch(q: string) {
  fireEvent.change(screen.getByPlaceholderText("artist – title"), { target: { value: q } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

// Each artist row's main button ("Explore <name>"); its "›" twin is "Explore artist".
const artistRows = () => screen.getAllByTitle(/^Explore /).filter((b) => b.title !== "Explore artist");

const artist = (dz_id: string, name: string, fans: number) =>
  ({ dz_id, name, image_url: "", track_count: 0, fans });

describe("Search — Deezer artists", () => {
  beforeEach(() => {
    cleanup();
    h.grabber = { enabled: true, spotify: { connected: true } };
    h.results = [];
    h.artists = [];
    h.resolve = {};
  });

  it("lists matching artists, three at first, and opens the browser for one", () => {
    h.artists = [artist("3", "Anyma", 45100), artist("1", "Anyma", 18), artist("2", "Anyma (UK)", 130), artist("4", "Anyma Fan", 1)];
    render(<Search />);
    runSearch("anyma");
    expect(artistRows()).toHaveLength(3);
    // Fan counts tell namesakes apart (compact format varies by locale).
    expect(artistRows()[0].textContent).toMatch(/45[.,]?1?\s?K fans on Deezer/i);
    fireEvent.click(screen.getByText("Show 1 more"));
    expect(artistRows()).toHaveLength(4);
    fireEvent.click(artistRows()[0]);
    expect(screen.getByTestId("artist-modal").textContent).toBe("Anyma:3");
  });

  it("opens each credit of a track row on Deezer, or says it isn't there", async () => {
    h.results = [{ spotify_track_id: "s1", title: "Song", artist: "Anyma, Grimes", album: "Al",
      album_artist: "Anyma", duration_ms: 1, isrc: "", track_no: 1, disc_no: 1, year: 2024, cover_url: "" }];
    h.resolve = { Grimes: { dz_id: "77", name: "Grimes", image_url: "" } };
    render(<Search />);
    runSearch("song");
    fireEvent.click(screen.getByTitle("Browse Grimes on Deezer"));
    await waitFor(() => expect(screen.getByTestId("artist-modal").textContent).toBe("Grimes:77"));
    fireEvent.click(screen.getByTitle("Browse Anyma on Deezer"));
    await waitFor(() => expect(screen.getByText(/“Anyma” isn't on Deezer/)).toBeTruthy());
  });

  it("searches artists even when Spotify isn't connected", () => {
    h.grabber = { enabled: true, spotify: { connected: false } };
    h.artists = [artist("3", "Anyma", 45100)];
    render(<Search />);
    expect(screen.getByText(/only artists \(from Deezer\) are searched/)).toBeTruthy();
    runSearch("anyma");
    expect(artistRows()).toHaveLength(1);
    expect(screen.queryByText("Tracks · Spotify")).toBeNull();
  });
});
