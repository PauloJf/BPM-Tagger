import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { Playlist } from "../lib/types";

// Playlists pulls in router/query/grabber context; mock them so the card grid
// renders in isolation and its PATCH calls can be asserted on.
const h = vi.hoisted(() => ({
  playlists: [] as unknown[],
  grabber: { enabled: false, spotify: { connected: false } } as Record<string, unknown>,
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: () => {} }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === "playlists"
      ? { data: { playlists: h.playlists }, isLoading: false, isError: false }
      : { data: undefined, isLoading: false, isError: false },
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

import Playlists from "./Playlists";
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
  vi.mocked(api.patch).mockClear();
  h.playlists = [];
  h.grabber = { enabled: false, spotify: { connected: false } };
});

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
