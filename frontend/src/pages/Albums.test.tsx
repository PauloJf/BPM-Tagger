import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const h = vi.hoisted(() => ({
  data: undefined as unknown,
  params: new URLSearchParams(),
  setParams: vi.fn(),
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
  useSearchParams: () => [h.params, h.setParams],
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === "albums" ? { data: h.data, isLoading: false } : { data: undefined },
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/LibraryTabs", () => ({ default: () => null }));
vi.mock("../components/Artwork", () => ({
  useArtwork: () => [false, () => {}],
  ArtToggle: () => null,
  Cover: () => null,
}));

import Albums, { albumBadge, type AlbumRow } from "./Albums";

function album(name: string, over: Partial<AlbumRow> = {}): AlbumRow {
  return { album: name, album_artist: "Someone", tracks: 10, year: 2001, avg_bpm: 120, sample_path: `/m/${name}.mp3`, ...over };
}
function respond(albums: AlbumRow[]) {
  h.data = { albums };
}
const titles = () => screen.getAllByRole("link").map((a) => a.querySelector(".album-title")?.textContent);

beforeEach(() => {
  cleanup();
  h.params = new URLSearchParams();
  h.setParams.mockReset();
  h.data = undefined;
});

describe("albumBadge", () => {
  it("shows the average BPM when no tempo is set", () => {
    expect(albumBadge(album("A", { avg_bpm: 128.4 }), false)?.text).toBe("avg 128.4 BPM");
    expect(albumBadge(album("A", { avg_bpm: null }), false)).toBeNull();
  });
  it("shows how many tracks fit when a tempo is set", () => {
    expect(albumBadge(album("A", { fit: 3, fit_folded: 0 }), true)?.text).toBe("3 of 10 fit");
  });
  it("says half-time when every match needed folding", () => {
    expect(albumBadge(album("A", { fit: 2, fit_folded: 2 }), true)?.text).toBe("2 half-time");
  });
  it("mentions folded matches in the hint when only some were", () => {
    const b = albumBadge(album("A", { fit: 3, fit_folded: 1 }), true)!;
    expect(b.text).toBe("3 of 10 fit");
    expect(b.hint).toContain("1 at half or double time");
  });
  it("does not call an album with nothing that fits half-time", () => {
    expect(albumBadge(album("A", { fit: 0, fit_folded: 0 }), true)?.text).toBe("0 of 10 fit");
  });
});

describe("Albums — without a tempo filter", () => {
  it("lists every album A–Z with its average BPM", () => {
    respond([album("Rumours", { avg_bpm: 120 }), album("Arrival", { avg_bpm: 100 })]);
    render(<Albums />);
    expect(titles()).toEqual(["Arrival", "Rumours"]);
    expect(screen.getByText("avg 100 BPM")).toBeTruthy();
  });

  it("offers no fit filter or fit sort", () => {
    respond([album("Arrival")]);
    render(<Albums />);
    expect(screen.queryByRole("button", { name: /With tracks that fit/ })).toBeNull();
    expect(screen.queryByRole("option", { name: "Most tracks that fit" })).toBeNull();
  });

  it("searches by album and by artist", () => {
    respond([album("Arrival", { album_artist: "ABBA" }), album("Rumours", { album_artist: "Fleetwood Mac" })]);
    render(<Albums />);
    fireEvent.change(screen.getByPlaceholderText("Search albums…"), { target: { value: "fleet" } });
    expect(titles()).toEqual(["Rumours"]);
  });

  it("links each card to the album page with its artist", () => {
    respond([album("Greatest Hits", { album_artist: "Queen" })]);
    render(<Albums />);
    expect(screen.getByRole("link").getAttribute("href"))
      .toBe("/album?album=Greatest%20Hits&album_artist=Queen");
  });

  it("sorts by track count on request", () => {
    respond([album("Small", { tracks: 3 }), album("Big", { tracks: 30 })]);
    render(<Albums />);
    fireEvent.change(screen.getByLabelText("Sort albums"), { target: { value: "tracks" } });
    expect(titles()).toEqual(["Big", "Small"]);
  });
});

describe("Albums — with a tempo filter", () => {
  beforeEach(() => {
    h.params = new URLSearchParams("bpm=155&bpm_tol=5");
    respond([
      album("Nothing Here", { fit: 0, fit_folded: 0 }),
      album("A Little", { fit: 1, fit_folded: 0 }),
      album("A Lot", { fit: 6, fit_folded: 0 }),
    ]);
  });

  it("shows only albums with something at this tempo, best fit first", () => {
    render(<Albums />);
    expect(titles()).toEqual(["A Lot", "A Little"]);
  });

  it("badges each cover with how many tracks fit", () => {
    render(<Albums />);
    expect(screen.getByText("6 of 10 fit")).toBeTruthy();
  });

  it("counts the fitting albums on the chip and in the strip", () => {
    render(<Albums />);
    expect(screen.getByRole("button", { name: "With tracks that fit 2" })).toBeTruthy();
    expect(screen.getByText("2 albums with tracks that fit")).toBeTruthy();
  });

  it("All albums brings back the ones with nothing at this tempo", () => {
    render(<Albums />);
    fireEvent.click(screen.getByRole("button", { name: /All albums/ }));
    expect(titles()).toContain("Nothing Here");
  });

  it("explains an empty result", () => {
    respond([album("Nothing Here", { fit: 0 })]);
    render(<Albums />);
    expect(screen.getByText(/No album has tracks at this tempo/)).toBeTruthy();
  });

  it("falls back to A–Z when the tempo is cleared", () => {
    cleanup();
    h.params = new URLSearchParams();
    respond([album("B"), album("A")]);
    render(<Albums />);
    expect(titles()).toEqual(["A", "B"]);
  });
});
