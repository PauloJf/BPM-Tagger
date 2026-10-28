import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";

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
    queryKey[0] === "artists" ? { data: h.data, isLoading: false } : { data: undefined },
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/LibraryTabs", () => ({ default: () => null }));
vi.mock("../components/Artwork", () => ({
  useArtwork: () => [false, () => {}],
  ArtToggle: () => null,
  ArtistImage: () => null,
}));

import Artists, { letterOf, type ArtistRow } from "./Artists";

const mix = (hot: number[] = []) => Array.from({ length: 16 }, (_, i) => (hot.includes(i) ? 3 : 0));

function artist(name: string, over: Partial<ArtistRow> = {}): ArtistRow {
  return { name, tracks: 10, albums: 2, avg_bpm: 120, sample_path: `/m/${name}.mp3`, mix: mix([6]), ...over };
}

function respond(artists: ArtistRow[]) {
  h.data = { artists, mix_range: [70, 190] };
}

const names = () => screen.getAllByRole("link").map((a) => a.querySelector(".artist-name")?.textContent);

beforeEach(() => {
  cleanup();
  h.params = new URLSearchParams();
  h.setParams.mockReset();
  h.data = undefined;
});

describe("letterOf", () => {
  it("uses the first letter, upper-cased", () => {
    expect(letterOf("abba")).toBe("A");
    expect(letterOf("  Zed")).toBe("Z");
  });
  it("sends digits, symbols and non-Latin names to #", () => {
    expect(letterOf("2Pac")).toBe("#");
    expect(letterOf("!!!")).toBe("#");
    expect(letterOf("")).toBe("#");
  });
});

describe("Artists — without a tempo filter", () => {
  it("lists everyone A–Z with their average BPM", () => {
    respond([artist("Queen", { avg_bpm: 130 }), artist("ABBA", { avg_bpm: 100 })]);
    render(<Artists />);
    expect(names()).toEqual(["ABBA", "Queen"]);
    expect(screen.getByText("Avg BPM")).toBeTruthy();
    expect(screen.getByText("130")).toBeTruthy();
  });

  it("offers no fit filter or fit sort", () => {
    respond([artist("ABBA")]);
    render(<Artists />);
    expect(screen.queryByRole("button", { name: /With tracks that fit/ })).toBeNull();
    expect(screen.queryByRole("option", { name: "Most tracks that fit" })).toBeNull();
  });

  it("filters by name as you type", () => {
    respond([artist("ABBA"), artist("Queen")]);
    render(<Artists />);
    fireEvent.change(screen.getByPlaceholderText("Search artists…"), { target: { value: "que" } });
    expect(names()).toEqual(["Queen"]);
  });

  it("sorts by track count on request", () => {
    respond([artist("ABBA", { tracks: 5 }), artist("Queen", { tracks: 90 })]);
    render(<Artists />);
    fireEvent.change(screen.getByLabelText("Sort artists"), { target: { value: "tracks" } });
    expect(names()).toEqual(["Queen", "ABBA"]);
  });

  it("links each row to the artist page", () => {
    respond([artist("Guns N' Roses")]);
    render(<Artists />);
    expect(screen.getByRole("link").getAttribute("href")).toBe("/artist?name=Guns%20N'%20Roses");
  });
});

describe("Artists — with a tempo filter", () => {
  beforeEach(() => {
    h.params = new URLSearchParams("bpm=155&bpm_tol=5");
    respond([
      artist("ABBA", { fit: 0, tracks: 40 }),
      artist("Queen", { fit: 11, tracks: 138 }),
      artist("OutKast", { fit: 12, tracks: 61 }),
    ]);
  });

  it("starts on the fit filter", () => {
    render(<Artists />);
    expect(screen.getByRole("button", { name: /With tracks that fit/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows only artists with something at this tempo, best fit first", () => {
    render(<Artists />);
    expect(names()).toEqual(["OutKast", "Queen"]);
  });

  it("says how many of each artist's tracks fit", () => {
    render(<Artists />);
    const row = screen.getByText("OutKast").closest("a")!;
    expect(row.textContent).toContain("12");
    expect(row.textContent).toContain("of 61");
    expect(screen.getByText("Fit")).toBeTruthy();
  });

  it("counts the fitting artists on the filter chip and in the strip", () => {
    render(<Artists />);
    expect(screen.getByRole("button", { name: "With tracks that fit 2" })).toBeTruthy();
    expect(screen.getByText("2 artists with tracks that fit")).toBeTruthy();
  });

  it("All artists brings back the ones with nothing at this tempo", () => {
    render(<Artists />);
    fireEvent.click(screen.getByRole("button", { name: /All artists/ }));
    expect(names()).toContain("ABBA");
  });

  it("describes each tempo mix for assistive tech", () => {
    render(<Artists />);
    expect(screen.getByLabelText("Tempo mix: 12 of 61 tracks fit")).toBeTruthy();
  });

  it("lights the bins the window reaches", () => {
    // 155 ± 5 → 150–160 BPM; bins are 7.5 wide from 70, so bin 10 (145–152.5)
    // and 11 (152.5–160) are lit, bin 6 (115–122.5) is not.
    respond([artist("OutKast", { fit: 12, mix: mix([6, 10, 11]) })]);
    render(<Artists />);
    const bars = within(screen.getByLabelText(/Tempo mix/)).getAllByText("", { selector: "span" });
    expect(bars[10].getAttribute("data-reach")).toBe("1");
    expect(bars[11].getAttribute("data-reach")).toBe("1");
    expect(bars[6].getAttribute("data-reach")).toBe("0");
  });

  it("explains an empty result instead of showing a blank list", () => {
    respond([artist("ABBA", { fit: 0 })]);
    render(<Artists />);
    expect(screen.getByText(/No artist has tracks at this tempo/)).toBeTruthy();
  });

  it("offers the fit sort, and falls back to A–Z when the tempo is cleared", () => {
    render(<Artists />);
    expect(screen.getByRole("option", { name: "Most tracks that fit" })).toBeTruthy();
    cleanup();
    h.params = new URLSearchParams();
    respond([artist("Queen"), artist("ABBA")]);
    render(<Artists />);
    expect(names()).toEqual(["ABBA", "Queen"]);
  });
});

describe("Artists — A–Z rail", () => {
  const many = () => Array.from({ length: 30 }, (_, i) => artist(`${String.fromCharCode(65 + (i % 3))} artist ${i}`));

  it("appears for a long A–Z list and disables letters nobody starts with", () => {
    respond(many());
    render(<Artists />);
    const rail = screen.getByRole("navigation", { name: "Jump to letter" });
    expect((within(rail).getByRole("button", { name: "A" }) as HTMLButtonElement).disabled).toBe(false);
    expect((within(rail).getByRole("button", { name: "Q" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("scrolls to the first artist of the letter", () => {
    respond(many());
    const target = document.createElement("div");
    const scroll = vi.fn();
    target.scrollIntoView = scroll;
    const spy = vi.spyOn(document, "getElementById").mockReturnValue(target);
    render(<Artists />);
    fireEvent.click(within(screen.getByRole("navigation", { name: "Jump to letter" })).getByRole("button", { name: "B" }));
    expect(spy).toHaveBeenCalledWith("artist-letter-B");
    expect(scroll).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("is left out of a short list", () => {
    respond([artist("ABBA"), artist("Queen")]);
    render(<Artists />);
    expect(screen.queryByRole("navigation", { name: "Jump to letter" })).toBeNull();
  });
});
