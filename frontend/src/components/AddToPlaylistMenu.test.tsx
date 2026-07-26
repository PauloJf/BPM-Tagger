import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import AddToPlaylistMenu from "./AddToPlaylistMenu";

// A mutable holder so each test can set the playlist list and inspect api.post.
const h = vi.hoisted(() => ({
  playlists: [] as Array<{ id: number; name: string; source: string }>,
  post: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: () => {} }),
  useQuery: () => ({ data: { playlists: h.playlists }, isLoading: false }),
  // Minimal useMutation: mutate runs the mutationFn then onSuccess/onError,
  // enough to drive the popover's add/import flow.
  useMutation: (opts: {
    mutationFn: (v: unknown) => Promise<unknown>;
    onSuccess?: (r: unknown, v: unknown) => void;
    onError?: (e: unknown) => void;
  }) => ({
    isPending: false,
    mutate: async (v: unknown) => {
      try {
        opts.onSuccess?.(await opts.mutationFn(v), v);
      } catch (e) {
        opts.onError?.(e);
      }
    },
  }),
}));

vi.mock("../lib/api", () => ({
  api: { get: vi.fn(), post: (...a: unknown[]) => h.post(...a) },
  ApiError: class extends Error {},
}));

beforeEach(() => {
  cleanup();
  h.playlists = [
    { id: 1, name: "Alpha", source: "local" },
    { id: 2, name: "Beta", source: "local" },
    { id: 9, name: "Remote", source: "spotify" },
  ];
  h.post.mockReset();
});

describe("AddToPlaylistMenu — bulk import mode", () => {
  it("lists only local playlists other than the source, and reports import counts", async () => {
    h.post.mockResolvedValue({ counts: { added: 2, already_present: 1, skipped_missing: 3 } });
    render(<AddToPlaylistMenu importFrom={2} label="Add all to playlist…" />);

    fireEvent.click(screen.getByRole("button", { name: "Add to playlist" }));

    // Beta (the source) and Remote (non-local) are filtered out; only Alpha shows.
    expect(screen.getByRole("menuitem", { name: /Alpha/ })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /Beta/ })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /Remote/ })).toBeNull();

    fireEvent.click(screen.getByRole("menuitem", { name: /Alpha/ }));

    await waitFor(() =>
      expect(screen.getByText("Alpha: Added 2 · 1 already there · 3 not in library")).toBeTruthy());
    expect(h.post).toHaveBeenCalledWith("/api/playlists/1/import", { from_playlist_id: 2 });
  });

  it("omits the zero clauses when nothing was already-there or skipped", async () => {
    h.post.mockResolvedValue({ counts: { added: 5, already_present: 0, skipped_missing: 0 } });
    render(<AddToPlaylistMenu importFrom={9} />);

    fireEvent.click(screen.getByRole("button", { name: "Add to playlist" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Alpha/ }));

    await waitFor(() => expect(screen.getByText("Alpha: Added 5")).toBeTruthy());
  });
});

describe("AddToPlaylistMenu — single-track mode", () => {
  it("posts the track path and reports added / already-in", async () => {
    h.post.mockResolvedValue({ added: true });
    render(<AddToPlaylistMenu path="/music/a.mp3" />);

    fireEvent.click(screen.getByRole("button", { name: "Add to playlist" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Alpha/ }));

    await waitFor(() => expect(screen.getByText("Added to Alpha")).toBeTruthy());
    expect(h.post).toHaveBeenCalledWith("/api/playlists/1/tracks", { path: "/music/a.mp3" });
  });
});
