import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import type { RelatedArtist } from "../lib/types";
import PageHeader from "../components/PageHeader";
import GrabberGate from "../components/GrabberGate";
import ArtistModal from "../components/ArtistModal";
import { ArtistRow } from "../components/RelatedPanel";

type DzArtist = { dz_id: string; name: string; image_url: string };

// Artists shown before "Show more" — enough to catch the one you meant
// without pushing the track results off-screen.
const ARTISTS_SHOWN = 3;

interface SearchResult {
  spotify_track_id: string;
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  duration_ms: number | null;
  isrc: string;
  track_no: number | null;
  disc_no: number | null;
  year: number | null;
  cover_url: string;
  in_library?: boolean;
  library_path?: string;   // matched library file — the "in library" chip links to it
  queued?: boolean;
}

export default function Search() {
  useTitle("Search");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");

  const searchQ = useQuery({
    queryKey: ["spotify-search", query],
    queryFn: () => api.get<{ results: SearchResult[] }>(`/api/spotify/search?q=${encodeURIComponent(query)}`),
    enabled: status.data?.enabled === true && !!status.data?.spotify?.connected && !!query,
  });

  // Deezer artists matching the query: each opens the catalog browser (top
  // tracks, albums, singles — the artist page's "Browse Deezer").
  const artistsQ = useQuery({
    queryKey: ["deezer-search-artists", query],
    queryFn: () => api.get<{ artists: RelatedArtist[] }>(`/api/deezer/search-artists?q=${encodeURIComponent(query)}`),
    enabled: status.data?.enabled === true && !!query,
    staleTime: 5 * 60_000,
  });
  const [allArtists, setAllArtists] = useState(false);
  const [modalArtist, setModalArtist] = useState<{ dzId: string; name: string } | null>(null);
  const [resolving, setResolving] = useState("");
  const [notOnDeezer, setNotOnDeezer] = useState("");

  // A result row's artist credit → its Deezer artist (same lookup and cache as
  // the artist page's Browse Deezer button), then open the browser.
  async function openArtist(name: string) {
    setNotOnDeezer("");
    setResolving(name);
    try {
      const r = await qc.fetchQuery({
        queryKey: ["deezer-resolve", name],
        queryFn: () => api.get<{ artist: DzArtist | null }>(`/api/deezer/resolve?name=${encodeURIComponent(name)}`),
        staleTime: Infinity,
      });
      if (r.artist) setModalArtist({ dzId: r.artist.dz_id, name: r.artist.name });
      else setNotOnDeezer(name);
    } catch {
      setNotOnDeezer(name);
    } finally {
      setResolving("");
    }
  }

  const add = useMutation({
    mutationFn: (r: SearchResult) => api.post("/api/queue", {
      spotify_track_id: r.spotify_track_id, title: r.title, artist: r.artist, album: r.album,
      album_artist: r.album_artist, duration_ms: r.duration_ms, isrc: r.isrc,
      track_no: r.track_no, disc_no: r.disc_no, year: r.year, cover_url: r.cover_url,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["spotify-search"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: ["grabber-status"] });
    },
  });

  function search() {
    setQuery(input.trim());
    setAllArtists(false);
    setNotOnDeezer("");
  }

  const connected = status.data?.spotify?.connected;
  const results = searchQ.data?.results ?? [];
  const artists = artistsQ.data?.artists ?? [];
  const shownArtists = allArtists ? artists : artists.slice(0, ARTISTS_SHOWN);

  return (
    <GrabberGate title="Search & grab" subtitle="Find an artist to browse on Deezer, or a track on Spotify to queue for download.">
      <PageHeader title="Search & grab" subtitle="Find an artist to browse on Deezer, or a track on Spotify to queue for download." />

      {/* Deezer artists need no account, so the box works without Spotify;
          only the track results do. */}
      {status.data && !connected && (
        <div className="flash" style={{ background: "var(--warn-bg)", borderColor: "var(--warn-bd)", color: "var(--warn-fg)" }}>
          Spotify isn't connected, so only artists (from Deezer) are searched. <Link to="/settings" style={{ color: "inherit", textDecoration: "underline" }}>Connect it in Settings</Link> to search tracks.
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="artist – title"
          style={{ flex: 1, minWidth: 240 }}
          onKeyDown={(e) => { if (e.key === "Enter" && input.trim()) search(); }}
        />
        <button className="btn btn-primary btn-md" disabled={!input.trim()} onClick={search}>Search</button>
      </div>

      {searchQ.isError && (
        <div className="flash error">{searchQ.error instanceof ApiError ? searchQ.error.message : "Search failed"}</div>
      )}

      {notOnDeezer && <div className="flash">“{notOnDeezer}” isn't on Deezer.</div>}

      {artists.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <div className="section-label" style={{ alignItems: "center" }}>
            <span>Artists · Deezer</span>
            {artists.length > ARTISTS_SHOWN && (
              <button className="btn btn-bare btn-sm" onClick={() => setAllArtists((v) => !v)}>
                {allArtists ? "Show fewer" : `Show ${artists.length - ARTISTS_SHOWN} more`}
              </button>
            )}
          </div>
          <div className="card" style={{ padding: 0 }}>
            {shownArtists.map((a) => (
              <ArtistRow key={a.dz_id} a={a} onOpen={() => setModalArtist({ dzId: a.dz_id, name: a.name })} />
            ))}
          </div>
        </div>
      )}

      {connected && query && <div className="section-label"><span>Tracks · Spotify</span></div>}
      {(connected || !query) && <div className="tracks-table">
        {results.length === 0 ? (
          <div className="tracks-row-empty">{searchQ.isFetching ? "Searching…" : query ? "No results." : "Enter a search above."}</div>
        ) : (
          results.map((r) => (
            <div key={r.spotify_track_id} className="pl-track-row" style={{ gridTemplateColumns: "1fr auto" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.title}</div>
                <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {/* Spotify joins credits with ", " — each one opens its Deezer artist. */}
                  {r.artist.split(", ").map((name, i) => (
                    <span key={name + i}>
                      {i > 0 && ", "}
                      <button
                        type="button"
                        className="link-btn"
                        disabled={resolving === name}
                        onClick={() => openArtist(name)}
                        title={`Browse ${name} on Deezer`}
                      >{resolving === name ? "Opening…" : name}</button>
                    </span>
                  ))}
                  {r.album ? ` · ${r.album}` : ""}{r.year ? ` · ${r.year}` : ""}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {r.in_library ? (
                  // Link to the matched library track (same pattern as the
                  // Suggestions page's chip); plain chip if the path is absent.
                  r.library_path ? (
                    <Link className="chip chip--have" to={`/track?path=${encodeURIComponent(r.library_path)}`} title="Open the matching library track">✓ in library</Link>
                  ) : (
                    <span className="chip chip--have">✓ in library</span>
                  )
                ) : r.queued ? (
                  <span className="chip chip--queued">↓ queued</span>
                ) : (
                  <button className="btn btn-soft btn-sm" disabled={add.isPending} onClick={() => add.mutate(r)}>
                    Add to queue
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>}
      {!connected && query && !artistsQ.isFetching && artists.length === 0 && (
        <div className="tracks-table"><div className="tracks-row-empty">No artists found.</div></div>
      )}

      {modalArtist && (
        <ArtistModal dzId={modalArtist.dzId} name={modalArtist.name} onClose={() => setModalArtist(null)} />
      )}
    </GrabberGate>
  );
}
