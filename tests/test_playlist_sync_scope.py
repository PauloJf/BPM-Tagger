"""Which playlists reach the Spotify syncer.

The playlists table holds spotify / navidrome / local rows, but the grabber's
SpotifySync loop pulled every enabled row and handed each to a Spotify-only
sync. Local playlists have no spotify_id, so each pass logged:

    Playlist sync error (MyRun): Spotify GET /playlists/None -> 400
    {"error": {"status": 400, "message": "Invalid base62 id"}}

Every 30 minutes, and the failure landed in last_error where the UI reports it
as a Spotify problem. PeriodicSync was always fine — it dispatches per source
itself — so the filter is opt-in rather than baked into the shared query.
"""

import pytest

from bpm_tagger.db import BPMDatabase


@pytest.fixture
def db(tmp_path):
    d = BPMDatabase(str(tmp_path / "bpm.db"))
    d.add_playlist("37i9dQZF1DXcBWIGoYBM5M", "Today's Top Hits")
    d.add_local_playlist("MyRun")
    d.add_local_playlist("My songs")
    return d


def test_unfiltered_query_still_returns_every_source(db):
    """PeriodicSync relies on this: it routes navidrome/local/spotify itself."""
    names = {p["name"] for p in db.get_enabled_playlists()}
    assert names == {"Today's Top Hits", "MyRun", "My songs"}


def test_spotify_filter_excludes_local_playlists(db):
    rows = db.get_enabled_playlists(source="spotify")
    assert [p["name"] for p in rows] == ["Today's Top Hits"]
    assert all(p["spotify_id"] for p in rows)


def test_local_filter_returns_only_local(db):
    assert {p["name"] for p in db.get_enabled_playlists(source="local")} == {"MyRun", "My songs"}


def test_disabled_playlists_are_excluded_by_the_filter_too(db):
    pl = next(p for p in db.get_enabled_playlists(source="spotify"))
    db.set_playlist_enabled(pl["id"], False)
    assert db.get_enabled_playlists(source="spotify") == []


# ── the guard inside the syncer ──────────────────────────────────────────────
