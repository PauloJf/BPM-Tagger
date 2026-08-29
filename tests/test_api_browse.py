"""/api/artists and /api/albums — the library browse indexes."""

import sqlite3

from bpm_tagger.grabber.matching import normalize_artist_name, split_artist_credits


def _login(client):
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    # (file_path, artist, album_artist, album, year, bpm, status)
    tracks = [
        ("/m/va/guest.mp3", "Guest Star", "Various Artists", "Comp", 2020, 120.0, "done"),
        ("/m/va/guest2.mp3", "Other Guest", "Various Artists", "Comp", 2020, 140.0, "done"),
        ("/m/abba/one.mp3", "ABBA", "ABBA", "Arrival", 1976, 100.0, "done"),
        ("/m/abba/two.mp3", "ABBA", "ABBA", "Waterloo", 1974, 0, "done"),
        ("/m/solo/tagless.mp3", "Solo Act", None, None, None, 90.0, "done"),
        ("/m/gone/x.mp3", "Deleted Artist", "Deleted Artist", "Gone", 2000, 80.0, "deleted"),
        ("/m/none/untagged.mp3", None, None, None, None, None, "done"),
    ]
    for fp, artist, aa, album, year, bpm, status in tracks:
        cur = conn.execute(
            "INSERT INTO tracks (file_path, artist, album_artist, album, year, bpm, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fp, artist, aa, album, year, bpm, status),
        )
        track_id = cur.lastrowid
        seen = set()
        for raw in (artist, aa):
            for name in split_artist_credits(raw):
                key = normalize_artist_name(name)
                if key and key not in seen:
                    seen.add(key)
                    conn.execute(
                        "INSERT INTO track_artists (track_id, name, norm_name) VALUES (?, ?, ?)",
                        (track_id, name, key))
    conn.commit()
    conn.close()


def test_browse_requires_login(client):
    assert client.get("/api/artists").status_code == 401
    assert client.get("/api/albums").status_code == 401


def test_artists_index(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    artists = client.get("/api/artists").get_json()["artists"]
    by_name = {a["name"]: a for a in artists}

    # One card per individually-credited artist (via track_artists) — compilation
    # guests get their own entry alongside the "Various Artists" credit itself;
    # deleted and fully untagged tracks are excluded.
    assert sorted(by_name) == ["ABBA", "Guest Star", "Other Guest", "Solo Act", "Various Artists"]
    assert by_name["Various Artists"]["tracks"] == 2
    assert by_name["Various Artists"]["albums"] == 1
    assert by_name["Various Artists"]["avg_bpm"] == 130.0
    assert by_name["Guest Star"] == {"name": "Guest Star", "tracks": 1, "albums": 1,
                                     "avg_bpm": 120.0, "sample_path": "/m/va/guest.mp3"}
    assert by_name["Other Guest"] == {"name": "Other Guest", "tracks": 1, "albums": 1,
                                      "avg_bpm": 140.0, "sample_path": "/m/va/guest2.mp3"}
    # bpm=0 rows don't drag the average down.
    assert by_name["ABBA"] == {"name": "ABBA", "tracks": 2, "albums": 2, "avg_bpm": 100.0,
                               "sample_path": "/m/abba/one.mp3"}
    # No album tag → 0 albums, not 1.
    assert by_name["Solo Act"]["albums"] == 0


def test_albums_index(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    albums = client.get("/api/albums").get_json()["albums"]

    # Alphabetical, one row per album+album_artist, deleted/untagged excluded.
    assert [(a["album"], a["album_artist"]) for a in albums] == [
        ("Arrival", "ABBA"), ("Comp", "Various Artists"), ("Waterloo", "ABBA"),
    ]
    comp = albums[1]
    assert comp["tracks"] == 2
    assert comp["year"] == 2020
    assert comp["avg_bpm"] == 130.0
    # Every album row carries a representative file for cover-art thumbnails.
    assert comp["sample_path"] == "/m/va/guest.mp3"
