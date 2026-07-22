import { describe, it, expect } from "vitest";
import { seedArtists } from "./PlaylistSuggestions";
import type { PlaylistTrack } from "../lib/types";

function t(over: Partial<PlaylistTrack>): PlaylistTrack {
  return {
    id: 1, source_track_id: null, spotify_track_id: null, position: 0,
    title: "T", artist: "", album: "", album_artist: "", duration_ms: null,
    isrc: null, track_no: null, cover_url: null, match_status: "have",
    matched_file_path: null, derived_status: "have", is_new: 0,
    first_seen_at: null, removed_at: null, ...over,
  };
}

describe("seedArtists", () => {
  it("ranks artists by frequency, most-frequent first", () => {
    const tracks = [
      t({ artist: "A" }), t({ artist: "A" }), t({ artist: "A" }),
      t({ artist: "B" }), t({ artist: "B" }),
      t({ artist: "C" }),
    ];
    expect(seedArtists(tracks)).toEqual(["A", "B", "C"]);
  });

  it("prefers the matched-library artist over the source artist", () => {
    const tracks = [
      t({ artist: "Source", local_artist: "Library" }),
      t({ artist: "Source", local_artist: "Library" }),
    ];
    expect(seedArtists(tracks)).toEqual(["Library"]);
  });

  it("ignores blank artists and honours the limit", () => {
    const tracks = [
      t({ artist: "" }), t({ artist: "  " }),
      t({ artist: "A" }), t({ artist: "B" }), t({ artist: "C" }),
      t({ artist: "D" }), t({ artist: "E" }), t({ artist: "F" }),
    ];
    const seeds = seedArtists(tracks, 5);
    expect(seeds).toHaveLength(5);
    expect(seeds).not.toContain("");
  });
});
