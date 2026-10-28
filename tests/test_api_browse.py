"""/api/artists and /api/albums — the library browse indexes."""

import sqlite3

from bpm_tagger.matching import normalize_artist_name, split_artist_credits


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
    # The tempo mix has its own tests below; this one is about the index itself.
    by_name = {a["name"]: {k: v for k, v in a.items() if k != "mix"} for a in artists}

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


# ── Tempo mix and tempo-fit counts (the Artists page) ─────────────────────────

def _artist(client, **params):
    resp = client.get("/api/artists", query_string=params)
    assert resp.status_code == 200
    body = resp.get_json()
    return {a["name"]: a for a in body["artists"]}, body


def test_artists_carry_a_tempo_mix_over_a_fixed_range(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    by_name, body = _artist(client)

    assert body["mix_range"] == [70.0, 190.0]
    # 16 bins of 7.5 BPM from 70: 100 → bin 4, 120 → bin 6, 140 → bin 9.
    abba = by_name["ABBA"]["mix"]
    assert len(abba) == 16 and sum(abba) == 1  # the bpm=0 track has no tempo to place
    assert abba[4] == 1
    va = by_name["Various Artists"]["mix"]
    assert (va[6], va[9], sum(va)) == (1, 1, 2)
    # Without a tempo filter there is no fit count at all.
    assert "fit" not in by_name["ABBA"]


def test_artists_without_any_bpm_get_an_empty_mix(client, base_config):
    _login(client)
    _seed(base_config["db_path"])
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("UPDATE tracks SET bpm = NULL WHERE artist = 'Solo Act'")
    conn.commit()
    conn.close()

    by_name, _ = _artist(client)

    assert by_name["Solo Act"]["mix"] == [0] * 16


def test_fit_counts_the_tracks_inside_the_tempo_window(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    by_name, _ = _artist(client, bpm=120, bpm_tol=5)

    assert by_name["Guest Star"]["fit"] == 1      # 120
    assert by_name["Other Guest"]["fit"] == 0     # 140
    assert by_name["Various Artists"]["fit"] == 1  # 120 of its two tracks
    assert by_name["ABBA"]["fit"] == 0            # 100 and an untimed track


def test_fit_matches_half_and_double_time_only_when_asked(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    plain, _ = _artist(client, bpm=240, bpm_tol=5)
    folded, _ = _artist(client, bpm=240, bpm_tol=5, bpm_cadence=1)

    assert plain["Guest Star"]["fit"] == 0
    assert folded["Guest Star"]["fit"] == 1   # 120 is half of 240
    assert folded["Other Guest"]["fit"] == 0  # 140 isn't within ±5 of 120


def test_fit_agrees_with_the_track_listing_filter(client, base_config):
    """The Artists page and the Tracks table must never disagree about which
    tracks a tempo window holds."""
    _login(client)
    _seed(base_config["db_path"])

    for params in ({"bpm": 120, "bpm_tol": 5}, {"bpm": 240, "bpm_tol": 5, "bpm_cadence": 1},
                   {"bpm": 100, "bpm_tol": 25}):
        by_name, _ = _artist(client, **params)
        listed = client.get("/api/tracks", query_string=params).get_json()["total"]
        # Each seeded artist except the compilation's two guests and VA has one
        # credit per track, so summing artist fits would double count the VA
        # tracks; compare against the distinct artists that hold the tracks.
        solo_fit = sum(by_name[n]["fit"] for n in ("ABBA", "Solo Act"))
        guest_fit = sum(by_name[n]["fit"] for n in ("Guest Star", "Other Guest"))
        assert solo_fit + guest_fit == listed


# ── Tempo fit on the album index ──────────────────────────────────────────────

def _albums(client, **params):
    resp = client.get("/api/albums", query_string=params)
    assert resp.status_code == 200
    return {(a["album"], a["album_artist"]): a for a in resp.get_json()["albums"]}


def test_albums_have_no_fit_without_a_tempo_filter(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    comp = _albums(client)[("Comp", "Various Artists")]

    assert "fit" not in comp and "fit_folded" not in comp
    assert comp["tracks"] == 2  # the existing fields are untouched


def test_album_fit_counts_the_tracks_inside_the_window(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    albums = _albums(client, bpm=120, bpm_tol=5)

    assert albums[("Comp", "Various Artists")]["fit"] == 1   # the 120, not the 140
    assert albums[("Arrival", "ABBA")]["fit"] == 0
    assert albums[("Waterloo", "ABBA")]["fit"] == 0          # bpm 0 is "no tempo"
    assert all(a["fit_folded"] == 0 for a in albums.values())


def test_album_fit_separates_half_time_matches(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    plain = _albums(client, bpm=240, bpm_tol=5)
    folded = _albums(client, bpm=240, bpm_tol=5, bpm_cadence=1)

    assert plain[("Comp", "Various Artists")]["fit"] == 0
    comp = folded[("Comp", "Various Artists")]
    assert (comp["fit"], comp["fit_folded"]) == (1, 1)  # 120 is half of 240, no direct hit


def test_album_fit_agrees_with_the_track_listing_filter(client, base_config):
    _login(client)
    _seed(base_config["db_path"])

    for params in ({"bpm": 120, "bpm_tol": 5}, {"bpm": 240, "bpm_tol": 5, "bpm_cadence": 1},
                   {"bpm": 100, "bpm_tol": 25}):
        albums = _albums(client, **params)
        # Every seeded track with a tempo is on a tagged album except Solo Act's
        # (no album tag), so album fits plus Solo Act's own track equal the listing.
        solo = client.get("/api/tracks", query_string={**params, "q": "tagless"}).get_json()["total"]
        listed = client.get("/api/tracks", query_string=params).get_json()["total"]
        assert sum(a["fit"] for a in albums.values()) + solo == listed
