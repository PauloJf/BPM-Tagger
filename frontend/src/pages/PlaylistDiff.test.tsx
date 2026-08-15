import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// The buckets view pulls in a router Link and the save menu (which is itself a
// query-backed popover). Both are stubbed so the test is about the bucketing.
vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));
vi.mock("../components/AddToPlaylistMenu", () => ({
  default: ({ label, paths }: { label?: string; paths?: string[] }) =>
    <button data-testid="save-as" data-paths={(paths ?? []).join("|")}>{label}</button>,
}));

import { DiffBuckets, type PlaylistDiffResponse, type DiffTrack } from "./PlaylistDiff";

const track = (over: Partial<DiffTrack> = {}): DiffTrack => ({
  row_id: 1,
  title: "Song",
  artist: "Artist",
  album: "Album",
  path: "/music/song.mp3",
  matched: true,
  bpm: 152.4,
  isrc: null,
  duration_ms: 195_000,
  cover_url: null,
  status: "have",
  ...over,
});

const payload = (over: Partial<PlaylistDiffResponse> = {}): PlaylistDiffResponse => {
  const both = [
    { a: track({ row_id: 1, title: "Shared" }), b: track({ row_id: 11, title: "Shared" }), same_file: true },
    { a: track({ row_id: 2, title: "Twin", path: "/music/twin.mp3" }),
      b: track({ row_id: 12, title: "Twin Remaster", path: "/music/twin.m4a" }), same_file: false },
  ];
  const only_a = [track({ row_id: 3, title: "Mine", path: "/music/mine.mp3" })];
  const only_b = [
    track({ row_id: 13, title: "Yours", path: "/music/yours.mp3" }),
    track({ row_id: 14, title: "Wanted", path: null, matched: false, bpm: null, status: "missing" }),
  ];
  return {
    a: { id: 1, name: "Morning", source: "local", count: 3 },
    b: { id: 2, name: "Evening", source: "spotify", count: 4 },
    both, only_a, only_b,
    counts: { both: both.length, only_a: only_a.length, only_b: only_b.length },
    paths: {
      both: ["/music/song.mp3", "/music/twin.mp3"],
      only_a: ["/music/mine.mp3"],
      only_b: ["/music/yours.mp3"],
    },
    ...over,
  };
};

beforeEach(cleanup);

describe("PlaylistDiff — the three buckets", () => {
  it("labels each tab with its playlist name and count", () => {
    render(<DiffBuckets data={payload()} />);
    expect(screen.getByText("In both · 2")).toBeTruthy();
    expect(screen.getByText("Only in Morning · 1")).toBeTruthy();
    expect(screen.getByText("Only in Evening · 2")).toBeTruthy();
  });

  it("opens on the shared bucket, showing the A side of each pair", () => {
    render(<DiffBuckets data={payload()} />);
    expect(screen.getByText("Shared")).toBeTruthy();
    expect(screen.getByText("Twin")).toBeTruthy();
    // The other side's exclusive tracks aren't on this tab.
    expect(screen.queryByText("Mine")).toBeNull();
  });

  it("flags a shared song the two playlists hold as different files", () => {
    render(<DiffBuckets data={payload()} />);
    expect(screen.getByText(/different file in Evening/)).toBeTruthy();
  });

  it("switches buckets on click", () => {
    render(<DiffBuckets data={payload()} />);
    fireEvent.click(screen.getByText("Only in Evening · 2"));
    expect(screen.getByText("Yours")).toBeTruthy();
    expect(screen.getByText("Wanted")).toBeTruthy();
    expect(screen.queryByText("Shared")).toBeNull();
  });

  it("marks which rows are in the library", () => {
    render(<DiffBuckets data={payload()} />);
    fireEvent.click(screen.getByText("Only in Evening · 2"));
    expect(screen.getAllByText("✓ in library")).toHaveLength(1);
    expect(screen.getAllByText("✗ not in library")).toHaveLength(1);
  });

  it("links a library-backed row to its track page and leaves an unmatched one plain", () => {
    const { container } = render(<DiffBuckets data={payload()} />);
    fireEvent.click(screen.getByText("Only in Evening · 2"));
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/track?path=%2Fmusic%2Fyours.mp3");
    expect(hrefs.filter((h) => h?.startsWith("/track"))).toHaveLength(1);
  });

  it("hands the current bucket's library paths to the save action", () => {
    render(<DiffBuckets data={payload()} />);
    expect(screen.getByTestId("save-as").getAttribute("data-paths"))
      .toBe("/music/song.mp3|/music/twin.mp3");
    expect(screen.getByText("Save as playlist… (2)")).toBeTruthy();

    fireEvent.click(screen.getByText("Only in Morning · 1"));
    expect(screen.getByTestId("save-as").getAttribute("data-paths")).toBe("/music/mine.mp3");
  });

  it("says how many of a bucket can't be saved, and offers no save at all when none can", () => {
    render(<DiffBuckets data={payload()} />);
    fireEvent.click(screen.getByText("Only in Evening · 2"));
    expect(screen.getByText(/1 of these 2 aren't in your library/)).toBeTruthy();

    cleanup();
    render(<DiffBuckets data={payload({
      counts: { both: 0, only_a: 0, only_b: 1 },
      both: [], only_a: [],
      paths: { both: [], only_a: [], only_b: [] },
    })} />);
    fireEvent.click(screen.getByText("Only in Evening · 1"));
    expect(screen.queryByTestId("save-as")).toBeNull();
  });

  it("says so rather than rendering an empty table for an empty bucket", () => {
    render(<DiffBuckets data={payload({
      counts: { both: 0, only_a: 1, only_b: 2 }, both: [],
    })} />);
    expect(screen.getByText("Nothing in this bucket.")).toBeTruthy();
  });
});
