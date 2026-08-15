"""Playlist operations: diff, merge, split — all LOCAL-FIRST.

Three set operations over playlists you already watch, which never touch the
sources they read from. Every output is a **Local** playlist: nothing here writes
to Spotify or to Navidrome, which stay the servers of record. Reading a Spotify
playlist to build a Local one is the whole point; pushing anything back is out of
scope by design, not by omission.

* ``GET  /api/playlists/diff?a=&b=``  → the ``both`` / ``only_a`` / ``only_b``
  buckets of two playlists.
* ``POST /api/playlists/merge``       → the union of several playlists' tracks,
  copied into one Local playlist.
* ``POST /api/playlists/<id>/split``  → one playlist fanned out into several
  Local ones, by run cadence or by album artist.

Two pieces of shared machinery do the actual thinking, and neither is restated
here:

* **Identity** — whether two playlist rows are the same recording. The chain is
  matched library file → ISRC → normalized artist+title, built on the same
  ``norm_artist``/``norm_title`` columns and ``grabber.matching`` normalizers the
  duplicates view and the library matcher use (see ``identity_keys``).
* **Cadence eligibility** — which tracks a run at some BPM could actually use.
  That is ``api.run._eligible`` over ``get_run_candidates()``, the one rule the
  run queue itself turns on, so a cadence split can never disagree with the queue
  it is splitting for.

Admin-only: deliberately absent from ``_PLAYER_ALLOWED`` in ``web/app.py``, which
is default-deny — playlist management has never been a player-role capability.
"""

import os

from flask import Blueprint, jsonify, request

from ...grabber.matching import normalize_artist, normalize_title
from ..auth import _check_csrf, login_required
from ..state import state
from .run import _eligible, _presets, _run_settings

playlist_ops_bp = Blueprint("api_playlist_ops", __name__)

# Ceilings. Neither is a limit anyone reaches by hand; they exist so one request
# can't be turned into an unbounded amount of writing.
_MAX_MERGE_SOURCES = 25
_MAX_SPLIT_GROUPS = 50

# An artist needs at least this many tracks in the playlist to be worth its own
# playlist — below it you get a shelf of one-track playlists instead of a split.
_MIN_ARTIST_GROUP = 3

# Separator between the source playlist's name and the group's in a split output,
# and the ceiling the composed name is held to (the same 200 the rename endpoint
# enforces).
_SPLIT_SEP = " · "
_MAX_NAME = 200


def _split_name(source_name: str, group: str) -> str:
    """``<playlist> · <group>``, trimmed to fit by shortening the *source* half.

    Which half gives is the whole point: truncating the tail would let two
    presets of a very long playlist name collapse onto one output playlist and
    silently merge two groups. The group label always survives intact, so the
    names stay distinct."""
    tail = f"{_SPLIT_SEP}{group}"[:_MAX_NAME]
    head = source_name[:max(0, _MAX_NAME - len(tail))]
    return f"{head}{tail}"


# ── Identity ──────────────────────────────────────────────────────────────────

def _row_path(row: dict) -> str | None:
    """The live library file this row resolves to, or None if it has none.

    ``local_file_path`` (from ``get_playlist_tracks``' LEFT JOIN) is the proof
    the join actually landed on a non-deleted library row — ``matched_file_path``
    alone can point at a file that has since been removed. ``file_path`` is what
    the already-matched queries (``get_playlist_matched_rows``,
    ``get_run_candidates``) call the same thing."""
    if row.get("file_path"):
        return str(row["file_path"])
    if row.get("match_status") == "have" and row.get("local_file_path"):
        return str(row["local_file_path"])
    return None


def identity_keys(row: dict) -> list[str]:
    """Every identity a playlist row can be matched on, strongest first:

    1. ``f:<path>``            — the library file it resolves to
    2. ``i:<ISRC>``            — its ISRC, upper-cased
    3. ``n:<artist>|<title>``  — its normalized artist + title

    A row contributes *all* the keys it has, not only its strongest. That is what
    makes the chain a chain: two playlists holding the same song as two different
    files still meet on the ISRC or the normalized tags, and a row with no
    library match at all (a Spotify track you don't own) still has an identity to
    be diffed on. A row with none of the three is unidentifiable and gets an empty
    list — the caller keeps it as its own singleton rather than merging every such
    row into one bucket.

    Normalization reuses ``grabber.matching`` — the stored ``norm_artist`` /
    ``norm_title`` columns are what the scanner and the Spotify sync wrote with
    those same functions, so the fallback for a row that predates them lands on
    the identical value."""
    keys: list[str] = []
    path = _row_path(row)
    if path:
        keys.append(f"f:{path}")
    isrc = str(row.get("isrc") or "").strip().upper()
    if isrc:
        keys.append(f"i:{isrc}")
    title = str(row.get("norm_title") or "").strip() or normalize_title(row.get("title"))
    if title:
        artist = str(row.get("norm_artist") or "").strip() or normalize_artist(
            row.get("artist") or row.get("album_artist"))
        keys.append(f"n:{artist}|{title}")
    return keys


def cluster_rows(rows: list[dict]) -> list[list[dict]]:
    """Group rows that are the same recording, preserving input order.

    Union-find over every key each row carries — the same two-key clustering
    ``get_duplicates()`` does for the duplicates view, with the file-path key
    added. Clusters come back in first-seen order and each keeps its rows in
    input order, so a caller can take "the first row from side A" and get a
    stable answer.

    Rows with no identity at all (``identity_keys`` empty) each form their own
    cluster rather than collapsing together."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:                  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    first_for_key: dict[str, int] = {}
    for i, row in enumerate(rows):
        find(i)                                   # register even a keyless row
        for key in identity_keys(row):
            other = first_for_key.get(key)
            if other is None:
                first_for_key[key] = i
            else:
                union(i, other)

    clusters: dict[int, list[dict]] = {}
    order: list[int] = []
    for i, row in enumerate(rows):
        root = find(i)
        if root not in clusters:
            clusters[root] = []
            order.append(root)
        clusters[root].append(row)
    return [clusters[r] for r in order]


# ── Row payloads ──────────────────────────────────────────────────────────────

def _live_rows(db, playlist_id: int) -> list[dict]:
    """A playlist's live membership: every non-tombstone row, matched or not.

    Broader than ``get_playlist_matched_rows`` on purpose — a diff has to be able
    to say "this track is in A and missing from your library entirely", which a
    matched-only query can't express. The library-backed subset of this
    (``_row_path`` non-None) is the same set that query returns, minus its
    dedupe-by-file, which the identity clustering handles instead."""
    return [r for r in db.get_playlist_tracks(playlist_id)
            if r.get("derived_status") != "removed"]


def _track_payload(row: dict) -> dict:
    """One diff row, shaped like the playlist detail page's rows.

    Library values first, source metadata second — a matched row's own tags are
    the truth and the source's are all an unmatched row has, exactly the
    precedence PlaylistDetail applies client-side."""
    path = _row_path(row)
    title = row.get("title") or (os.path.splitext(os.path.basename(path))[0] if path else "")
    return {
        "row_id": row.get("id"),
        "title": title,
        "artist": row.get("local_artist") or row.get("artist") or "",
        "album": row.get("local_album") or row.get("album") or "",
        "path": path,
        "matched": path is not None,
        "bpm": row.get("local_bpm"),
        "isrc": row.get("isrc"),
        "duration_ms": row.get("local_duration_ms") or row.get("duration_ms"),
        "cover_url": row.get("cover_url") or None,
        "status": row.get("derived_status"),
    }


def _representative(rows: list[dict]) -> dict:
    """The row that stands for a side of a cluster: a library-backed one if there
    is one (it carries BPM, real tags and a playable path), else the first."""
    for r in rows:
        if _row_path(r):
            return r
    return rows[0]


def _bucket_paths(entries: list[dict]) -> list[str]:
    """The distinct library paths in a bucket, in order — what "Save as
    playlist…" writes. Unmatched rows have nothing to save and drop out here
    rather than being reported as failures downstream.

    ``both`` entries are ``{a, b, same_file}`` pairs and the A side is the one
    that gets saved (it's the side the client lists); the one-sided buckets hold
    bare payloads."""
    out, seen = [], set()
    for entry in entries:
        payload = entry["a"] if "a" in entry else entry
        path = payload.get("path")
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


# ── Diff ──────────────────────────────────────────────────────────────────────

@playlist_ops_bp.route("/api/playlists/diff")
@login_required
def playlist_diff():
    """Two playlists as three buckets: in both, only in A, only in B.

    Membership is compared by the identity chain, not by row id or file path
    alone, so a track you own twice — once per playlist, as two different files —
    reads as "in both" rather than as two one-sided entries. ``both`` entries
    carry each side's row, and ``same_file`` says whether the two sides are
    literally the same file on disk.

    ``paths`` mirrors each bucket's library-backed tracks, so the client's "Save
    as playlist…" can hand them straight to the existing bulk-add endpoint."""
    db = state().db
    ids, err = _parse_diff_ids()
    if err:
        return err
    a_id, b_id = ids
    a_pl, b_pl = db.get_playlist(a_id), db.get_playlist(b_id)
    if not a_pl:
        return jsonify(error="not_found", playlist_id=a_id), 404
    if not b_pl:
        return jsonify(error="not_found", playlist_id=b_id), 404

    rows_a, rows_b = _live_rows(db, a_id), _live_rows(db, b_id)
    # One clustering over both sides; the marker rides on a copy so the DB rows
    # stay untouched for anyone else reading them.
    tagged = ([dict(r, _side="a") for r in rows_a] +
              [dict(r, _side="b") for r in rows_b])

    both, only_a, only_b = [], [], []
    for cluster in cluster_rows(tagged):
        side_a = [r for r in cluster if r["_side"] == "a"]
        side_b = [r for r in cluster if r["_side"] == "b"]
        if side_a and side_b:
            ra, rb = _representative(side_a), _representative(side_b)
            pa, pb = _row_path(ra), _row_path(rb)
            both.append({"a": _track_payload(ra), "b": _track_payload(rb),
                         "same_file": bool(pa) and pa == pb})
        elif side_a:
            only_a.append(_track_payload(_representative(side_a)))
        else:
            only_b.append(_track_payload(_representative(side_b)))

    def meta(pl, rows):
        return {"id": pl["id"], "name": pl["name"], "source": pl.get("source") or "spotify",
                "count": len(rows)}

    return jsonify(
        a=meta(a_pl, rows_a), b=meta(b_pl, rows_b),
        both=both, only_a=only_a, only_b=only_b,
        counts={"both": len(both), "only_a": len(only_a), "only_b": len(only_b)},
        paths={"both": _bucket_paths(both), "only_a": _bucket_paths(only_a),
               "only_b": _bucket_paths(only_b)},
    )


def _parse_diff_ids():
    """(a_id, b_id), or (None, error_response). Both are required and must differ
    — diffing a playlist against itself is a page of "both" and a question the
    user didn't mean to ask."""
    try:
        a_id = int(request.args.get("a"))
        b_id = int(request.args.get("b"))
    except (TypeError, ValueError):
        return None, (jsonify(error="Two playlist ids ('a' and 'b') are required."), 400)
    if a_id == b_id:
        return None, (jsonify(error="Pick two different playlists to compare."), 400)
    return (a_id, b_id), None


# ── Merge ─────────────────────────────────────────────────────────────────────

def _resolve_merge_target(db, target):
    """(playlist_row, None) or (None, error_response).

    ``{"id": n}`` picks an existing Local playlist; ``{"name": "..."}`` creates
    one. Local-only, and refused rather than silently redirected: merging into a
    synced mirror would either be undone by its next sync or need a write back to
    the source, and this whole module is local-first."""
    if not isinstance(target, dict):
        return None, (jsonify(error="A merge target ({id} or {name}) is required."), 400)
    if target.get("id") is not None:
        try:
            tid = int(target["id"])
        except (TypeError, ValueError):
            return None, (jsonify(error="The merge target id must be a playlist id."), 400)
        pl = db.get_playlist(tid)
        if not pl:
            return None, (jsonify(error="not_found", playlist_id=tid), 404)
        if pl.get("source") != "local":
            return None, (jsonify(error="Playlists can only be merged into a local "
                                        "playlist — a synced one takes its tracks from "
                                        "its source."), 400)
        return pl, None
    name = str(target.get("name") or "").strip()
    if not name:
        return None, (jsonify(error="A merge target ({id} or {name}) is required."), 400)
    if len(name) > 200:
        return None, (jsonify(error="That name is too long (200 characters max)."), 400)
    return db.get_playlist(db.add_local_playlist(name)), None


@playlist_ops_bp.route("/api/playlists/merge", methods=["POST"])
@login_required
def playlist_merge():
    """Copy the union of several playlists' library-backed tracks into one Local
    playlist.

    Deduped by the same identity chain the diff uses, across sources as well as
    within each one, first occurrence winning — so a track in three of the four
    sources is added once and reported as a duplicate skip by the other two. The
    per-source report distinguishes the three ways a track can fail to be added:

    * ``skipped_duplicate`` — an earlier source already contributed it;
    * ``already_present``   — the target already had it (re-running a merge is a
      no-op, since the target's rows key on the library file path);
    * ``not_in_library``    — the row has no file on disk to copy.

    Sources may be any playlist type; the target must be Local."""
    _check_csrf()
    db = state().db
    data = request.get_json(force=True, silent=True) or {}

    raw = data.get("source_ids")
    if not isinstance(raw, list) or not raw:
        return jsonify(error="A 'source_ids' list of playlist ids is required."), 400
    try:
        source_ids = list(dict.fromkeys(int(v) for v in raw))
    except (TypeError, ValueError):
        return jsonify(error="A 'source_ids' list of playlist ids is required."), 400
    if len(source_ids) > _MAX_MERGE_SOURCES:
        return jsonify(error=f"Too many playlists at once (max {_MAX_MERGE_SOURCES})."), 400
    sources = []
    for sid in source_ids:
        pl = db.get_playlist(sid)
        if not pl:
            return jsonify(error="not_found", playlist_id=sid), 404
        sources.append(pl)

    target, err = _resolve_merge_target(db, data.get("target"))
    if err:
        return err

    seen: set[str] = set()
    report, totals = [], {"added": 0, "already_present": 0,
                          "skipped_duplicate": 0, "not_in_library": 0}
    for pl in sources:
        paths, duplicate, unbacked = [], 0, 0
        for row in _live_rows(db, pl["id"]):
            path = _row_path(row)
            if not path:
                unbacked += 1
                continue
            keys = identity_keys(row)
            if any(k in seen for k in keys):
                duplicate += 1
                continue
            seen.update(keys)
            paths.append(path)
        counts = (db.add_tracks_to_local_playlist(target["id"], paths) if paths else
                  {"added": 0, "already_present": 0, "skipped_missing": 0})
        entry = {
            "id": pl["id"], "name": pl["name"],
            "added": counts["added"],
            "already_present": counts["already_present"],
            "skipped_duplicate": duplicate,
            # A path that vanished between the read and the write is, from the
            # user's side, the same outcome as a row that never had a file.
            "not_in_library": unbacked + counts["skipped_missing"],
        }
        report.append(entry)
        for k in totals:
            totals[k] += entry[k]

    return jsonify(ok=True, target=db.get_playlist(target["id"], with_counts=True),
                   sources=report, totals=totals)


# ── Split ─────────────────────────────────────────────────────────────────────

def _cadence_groups(st, playlist_id: int) -> tuple[list[tuple[str, list[str]]], list[dict]]:
    """(groups, skipped) for a split by run cadence.

    One group per configured run preset, holding exactly the tracks a run at that
    preset would draw from this playlist: ``get_run_candidates(playlist_id)``
    filtered by ``_eligible`` with the user's own octave-fold and stretch-limit
    settings. That is the run queue's own rule and its own candidate pool — the
    only difference is that this keeps the whole eligible set instead of a
    shuffled, count-capped slice, and orders it closest-cadence-first the way
    /api/run/ready does.

    A track can legitimately land in several groups: at ±15% a 158 BPM song is
    runnable at both 155 and 165, and pretending otherwise would make one of the
    two playlists lie about what you could run."""
    octave, limit = _run_settings(st.config)
    candidates = st.db.get_run_candidates(playlist_id)
    groups, skipped = [], []
    for preset in _presets(st.config):
        found = _eligible(candidates, float(preset["bpm"]), octave, limit)
        found.sort(key=lambda x: x[2])            # closest to the cadence first
        paths = [t["file_path"] for (t, _folded, _dev) in found]
        if paths:
            groups.append((preset["name"], paths))
        else:
            skipped.append({"group": preset["name"], "count": 0, "reason": "empty"})
    return groups, skipped


def _cadence_preview(st, playlist_id: int) -> dict:
    """Per-preset eligible counts without writing anything — the same numbers the
    stats strip's ``runnable`` field reports, since both fold the same candidate
    pool with the same rule."""
    groups, skipped = _cadence_groups(st, playlist_id)
    counts = {name: len(paths) for name, paths in groups}
    counts.update({s["group"]: 0 for s in skipped})
    return counts


def _artist_groups(st, playlist_id: int) -> tuple[list[tuple[str, list[str]]], list[dict]]:
    """(groups, skipped) for a split by artist.

    Grouped on ``album_artist`` falling back to ``artist`` — the same key the
    Artists browse groups on (``list_artists``), so a split's playlists line up
    with the artist pages rather than fragmenting compilations by guest credit.
    Only artists with at least ``_MIN_ARTIST_GROUP`` tracks get a playlist; the
    rest are reported, because a split that produced thirty one-track playlists
    would be worse than no split at all."""
    groups, skipped = [], []
    buckets: dict[str, list[str]] = {}
    untagged = 0
    for row in st.db.get_playlist_matched_rows(playlist_id):
        name = (str(row.get("album_artist") or "").strip()
                or str(row.get("artist") or "").strip())
        if not name:
            untagged += 1
            continue
        buckets.setdefault(name, []).append(row["file_path"])
    for name in sorted(buckets, key=lambda n: n.casefold()):
        paths = buckets[name]
        if len(paths) >= _MIN_ARTIST_GROUP:
            groups.append((name, paths))
        else:
            skipped.append({"group": name, "count": len(paths), "reason": "too_small"})
    if untagged:
        skipped.append({"group": "", "count": untagged, "reason": "no_artist"})
    return groups, skipped


@playlist_ops_bp.route("/api/playlists/<int:pid>/split", methods=["POST"])
@login_required
def playlist_split(pid):
    """Fan one playlist out into several Local playlists, by cadence or by artist.

    Each group becomes ``<playlist name> · <group>``. Existing playlists of that
    name are topped up rather than duplicated, and the underlying bulk add keys on
    the library file path, so re-running a split after the source grew adds only
    what's new — the whole operation is idempotent.

    The source may be any playlist type (that's the point: slicing a Spotify
    mirror into runnable Local playlists); the outputs are always Local."""
    _check_csrf()
    st = state()
    pl = st.db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found", playlist_id=pid), 404
    data = request.get_json(force=True, silent=True) or {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode == "cadence":
        groups, skipped = _cadence_groups(st, pid)
    elif mode == "artist":
        groups, skipped = _artist_groups(st, pid)
    else:
        return jsonify(error="mode must be 'cadence' or 'artist'."), 400

    created = []
    for label, paths in groups[:_MAX_SPLIT_GROUPS]:
        name = _split_name(pl["name"], label)
        existing = st.db.find_local_playlist_by_name(name)
        target_id = existing["id"] if existing else st.db.add_local_playlist(name)
        counts = st.db.add_tracks_to_local_playlist(target_id, paths)
        created.append({
            "id": target_id, "name": name, "group": label,
            "created": existing is None, "eligible": len(paths),
            "added": counts["added"], "already_present": counts["already_present"],
            "skipped_missing": counts["skipped_missing"],
        })
    for label, paths in groups[_MAX_SPLIT_GROUPS:]:
        skipped.append({"group": label, "count": len(paths), "reason": "limit"})

    return jsonify(ok=True, mode=mode, source={"id": pl["id"], "name": pl["name"]},
                   playlists=created, skipped=skipped)


@playlist_ops_bp.route("/api/playlists/<int:pid>/split")
@login_required
def playlist_split_preview(pid):
    """What a split would produce, without producing it — the dialog's preview.

    Cadence counts are the same ``runnable`` numbers the stats strip shows (same
    pool, same rule); artist groups come back already thresholded, so the dialog
    can say "6 playlists, 11 tracks left over" before the user commits."""
    st = state()
    pl = st.db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found", playlist_id=pid), 404
    artist_groups, artist_skipped = _artist_groups(st, pid)
    return jsonify(
        source={"id": pl["id"], "name": pl["name"]},
        presets=_presets(st.config),
        cadence=_cadence_preview(st, pid),
        artist={
            "groups": [{"group": name, "count": len(paths)} for name, paths in artist_groups],
            "skipped": artist_skipped,
            "min_group": _MIN_ARTIST_GROUP,
        },
    )
