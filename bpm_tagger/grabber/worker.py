"""GrabWorker — the download→transcode→tag→BPM pipeline (§5).

One GrabPipeline runs a claimed queue item end to end; GrabPool runs N worker
threads that claim pending items via DB CAS. Auto-accept (score ≥ threshold or
ISRC) downloads immediately; ambiguous/failed matches route to awaiting_user for
the M5 inbox. The BPM tag is written last and the DB hash is taken *after* that
write so the library watcher never re-analyzes a freshly grabbed file.
"""

import logging
import os
import shutil
import threading
import time

from ..bpm.loudness import analyze_loudness
from ..bpm.lyrics import is_synced, write_lyrics
from ..bpm.pipeline import detect_bpm
from ..bpm.tags import get_file_hash, write_bpm_tag
from ..integrations.lrclib import fetch_lyrics
from ..integrations.navidrome import _trigger_navidrome_rescan
from .matching import normalize_artist, normalize_title, score
from .path_template import render, unique_path
from .providers import build_providers
from .providers.base import ProviderCandidate, TrackMeta
from .tagging import embed_cover, fetch_cover, write_track_tags
from .transcode import profile_ext, transcode

log = logging.getLogger(__name__)


def _meta_from_item(item: dict) -> dict:
    return {k: item.get(k) for k in ("title", "artist", "album", "album_artist",
                                     "duration_ms", "isrc", "track_no", "disc_no",
                                     "year", "cover_url", "spotify_track_id")}


class GrabPipeline:
    def __init__(self, config, db, tagger, providers=None, notifier=None):
        self.config = config
        self.db = db
        self.tagger = tagger
        self.providers = providers if providers is not None else build_providers(config)
        self.notifier = notifier
        self.auto_accept = float(config.get("auto_accept_threshold", 0.85))
        self.ask = float(config.get("ask_threshold", 0.55))
        self.output_format = config.get("output_format", "mp3-320")
        self.path_template = config.get("path_template", "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}")
        self.music_dir = config["music_dir"]
        self.dry_run = bool(config.get("grab_dry_run", False))
        self.grab_tmp = os.path.join(os.path.dirname(os.path.abspath(config["db_path"])), "grab_tmp")

    def _provider(self, name):
        return next((p for p in self.providers if p.name == name), None)

    def _candidate_from_row(self, row: dict) -> ProviderCandidate:
        return ProviderCandidate(
            provider=row.get("provider") or "", provider_track_id=row.get("provider_track_id") or "",
            title=row.get("title") or "", artist=row.get("artist") or "",
            album=row.get("album") or "", duration_ms=row.get("duration_ms"),
            isrc=row.get("isrc") or "", quality=row.get("quality") or "",
            url=row.get("url") or "", cover_url=row.get("cover_url") or "")

    def _search_and_score(self, search_meta: TrackMeta, score_meta: TrackMeta) -> list:
        """Search using `search_meta` (may be a user override), score every
        candidate against `score_meta` (always the real track)."""
        found = []
        for provider in self.providers:
            try:
                cands = provider.search(search_meta, limit=8)
            except Exception as exc:
                log.warning("Provider %s search failed: %s", provider.name, exc)
                continue
            for c in cands:
                s, br = score(score_meta.as_match(), c.as_match())
                if provider.name == "ytdlp" and not c.is_topic:
                    s = max(0.0, s - 0.10)
                    br["yt_channel_penalty"] = 0.10
                c.score, c.score_breakdown = s, br
                found.append(c)
            # If a provider already yields an auto-accept, don't query the rest.
            if found and max(c.score for c in found) >= self.auto_accept:
                break
        found.sort(key=lambda c: c.score, reverse=True)
        for i, c in enumerate(found):
            c.rank = i
        return found

    def _download_with_fallback(self, item_id, candidates, tmp_dir):
        tried_candidates = 0
        for c in candidates:
            provider = self._provider(c.provider)
            if not provider:
                continue
            for _attempt in range(2):  # up to 2 attempts per candidate
                try:
                    df = provider.download(c, tmp_dir,
                                           progress_cb=lambda f: self.db.update_grab(item_id, progress=f))
                    return c, df
                except Exception as exc:
                    log.warning("Download failed (%s/%s): %s", c.provider, c.provider_track_id, exc)
            tried_candidates += 1
            if tried_candidates >= 3:  # fall through only a few before giving up
                break
        return None, None

    @staticmethod
    def _cand_row(c) -> dict:
        import json
        return {"provider": c.provider, "provider_track_id": c.provider_track_id,
                "title": c.title, "artist": c.artist, "album": c.album,
                "duration_ms": c.duration_ms, "isrc": c.isrc, "quality": c.quality,
                "score": c.score, "score_breakdown": json.dumps(c.score_breakdown),
                "url": c.url, "cover_url": c.cover_url, "rank": c.rank}

    def process_item(self, item: dict) -> str:
        """Run one queue item through the pipeline. Returns the terminal status."""
        item_id = item["id"]
        meta = _meta_from_item(item)
        meta["norm_title"] = normalize_title(meta.get("title"))
        meta["norm_artist"] = normalize_artist(meta.get("artist"))
        score_meta = TrackMeta.from_row(item)
        tmp_dir = os.path.join(self.grab_tmp, str(item_id))
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            # Inbox choice: skip search, download the user-picked candidate.
            chosen_id = item.get("chosen_candidate_id")
            if chosen_id:
                row = self.db.get_grab_candidate(chosen_id)
                if not row:
                    self.db.transition(item_id, "failed", "chosen candidate no longer available")
                    return "failed"
                self.db.transition(item_id, "downloading", "user choice")
                return self._download_and_finish(item, meta, [self._candidate_from_row(row)], tmp_dir)

            # 1 — search (honoring a user search override) + score against the real track
            self.db.transition(item_id, "searching")
            override = (item.get("search_override") or "").strip()
            search_meta = TrackMeta(title=override, artist="") if override else score_meta
            candidates = self._search_and_score(search_meta, score_meta)
            self.db.add_grab_candidates(item_id, [self._cand_row(c) for c in candidates])
            if not candidates:
                self.db.transition(item_id, "awaiting_user", "no candidates found")
                self._notify_ambiguous(item, None)
                return "awaiting_user"

            best = candidates[0]
            if self.dry_run:
                self.db.transition(item_id, "awaiting_user",
                                   f"dry run — best {best.score:.2f} ({best.provider})")
                return "awaiting_user"
            if best.score < self.auto_accept:
                self.db.transition(item_id, "awaiting_user",
                                   f"best {best.score:.2f} below auto-accept")
                self._notify_ambiguous(item, best)
                return "awaiting_user"

            self.db.transition(item_id, "downloading")
            return self._download_and_finish(item, meta, candidates, tmp_dir)
        except Exception as exc:
            log.exception("Grab item %s failed: %s", item_id, exc)
            self.db.update_grab(item_id, error=str(exc)[:500],
                                attempts=(item.get("attempts") or 0) + 1)
            self.db.transition(item_id, "failed", str(exc)[:200])
            return "failed"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _download_and_finish(self, item: dict, meta: dict, candidates: list, tmp_dir: str) -> str:
        """Download the best available candidate then transcode → tag → move →
        BPM → done. Shared by the auto-accept path and inbox 'choose'."""
        item_id = item["id"]
        chosen, downloaded = self._download_with_fallback(item_id, candidates, tmp_dir)
        if not downloaded:
            self.db.update_grab(item_id, error="all downloads failed",
                                attempts=(item.get("attempts") or 0) + 1)
            self.db.transition(item_id, "failed", "all downloads failed")
            self._notify_failure(item)
            return "failed"
        self.db.update_grab(item_id, provider=chosen.provider)

        # 3 — transcode to the single output format
        self.db.transition(item_id, "transcoding")
        out_path, warn = transcode(downloaded.path, tmp_dir, self.output_format, f"grab_{item_id}")
        if warn:
            self.db.transition(item_id, "transcoding", warn)

        # 4 — descriptive tags + cover. Tag/cover writes are non-fatal (the audio
        #     is fine), but record any failure as a warning event so it's visible
        #     instead of buried in the logs.
        self.db.transition(item_id, "tagging")
        tag_warn = write_track_tags(out_path, meta)
        if tag_warn:
            self.db.add_grab_event(item_id, "warning", tag_warn)
        cover = fetch_cover(meta.get("cover_url"))
        if cover:
            cover_warn = embed_cover(out_path, cover)
            if cover_warn:
                self.db.add_grab_event(item_id, "warning", cover_warn)
        # Lyrics (opt-in via lyrics_enabled): fetched from LRCLIB and embedded
        # while the file is still staged. Grabbed files always embed — a sidecar
        # would need its own move into music_dir. Non-fatal like tags/cover.
        lyrics_text = ""
        if self.config.get("lyrics_enabled"):
            try:
                lyr = fetch_lyrics(meta.get("artist") or "", meta.get("title") or "",
                                   meta.get("album") or "", meta.get("duration_ms"))
                lyrics_text = (lyr or {}).get("synced") or (lyr or {}).get("plain") or ""
                if lyrics_text and not write_lyrics(out_path, lyrics_text, mode="embed"):
                    lyrics_text = ""
            except Exception as exc:
                self.db.add_grab_event(item_id, "warning", f"lyrics fetch failed: {exc}")
                lyrics_text = ""

        # 5 — BPM: detect + write tag on the *staged* file (still in tmp_dir).
        #     Doing this before the file enters music_dir means a crash can't
        #     leave an untagged, DB-orphaned file in the library that would be
        #     re-grabbed as a duplicate on restart.
        self.db.transition(item_id, "analyzing_bpm")
        result = detect_bpm(out_path, self.config)
        if self.config.get("write_tags", True) and result.get("bpm"):
            write_bpm_tag(out_path, result["bpm"], self.config.get("preserve_mtime", True))
        # Loudness too, while the staged file is still warm. Measured (not read from
        # a tag) — we just transcoded, so any provider ReplayGain tag is stale.
        lufs = loudness_source = None
        if self.config.get("measure_loudness", True):
            lufs, loudness_source = analyze_loudness(out_path, prefer_tag=False)

        # 6 — render path + atomically place under music_dir (copy-then-replace:
        #     /data and /music may be different filesystems), then record the DB
        #     row immediately. copy2 preserves mtime so the hash taken here still
        #     matches the tagged file the watcher will later see (anti-loop).
        ext = profile_ext(self.output_format)
        final = unique_path(self.music_dir, render(self.path_template, meta, ext))
        os.makedirs(os.path.dirname(final), exist_ok=True)
        part = os.path.join(os.path.dirname(final), f".mg_part_{item_id}.{ext}")
        shutil.copy2(out_path, part)
        os.replace(part, final)
        fresh_hash = get_file_hash(final)
        self.db.record_managed_track(
            final, fresh_hash, meta, result.get("bpm"), result.get("bpm_dr"),
            result.get("bpm_es"), result.get("bpm_lb"), result.get("confidence"),
            result.get("detector"), meta.get("spotify_track_id") or "",
            loudness_lufs=lufs, loudness_source=loudness_source)
        if lyrics_text:
            self.db.set_lyrics_state(final, "fetched", is_synced(lyrics_text))

        # 7 — done + notify + debounced Navidrome rescan
        self.db.update_grab(item_id, progress=1.0, final_path=final)
        self.db.transition(item_id, "done", os.path.basename(final))
        self.db.bump_grabbed_total()   # all-time tally (survives queue cleanup)
        self._notify_done(item)
        try:
            _trigger_navidrome_rescan(self.config)
        except Exception:
            pass
        return "done"

    # ── notifications (ntfy never fails the pipeline) ──────────────────────────
    def _click_url(self, path):
        base = (self.config.get("ui_public_url") or "").rstrip("/")
        return f"{base}{path}" if base else ""

    def _notify_ambiguous(self, item, best):
        if not self.notifier:
            return
        body = (f"Best match {best.score:.2f} via {best.provider}. Resolve in the inbox."
                if best else "No confident match found. Resolve in the inbox.")
        url = self._click_url(f"/inbox/{item['id']}")
        try:
            self.notifier.send_grabber(
                f"Needs review: {item.get('artist')} – {item.get('title')}",
                body, click_url=url, priority="default", tags="mag",
                actions=f"view, Open inbox, {url}" if url else "")
        except Exception:
            pass

    def _notify_failure(self, item):
        if not self.notifier:
            return
        try:
            self.notifier.send_grabber(
                f"Grab failed: {item.get('artist')} – {item.get('title')}",
                "No provider could download this track.", priority="high", tags="x")
        except Exception:
            pass

    def _notify_done(self, item):
        if not self.notifier:
            return
        try:
            self.notifier.send_grabber(
                f"↓ {item.get('artist')} – {item.get('title')}", "Downloaded and tagged.",
                priority="low", tags="arrow_down")
        except Exception:
            pass


class GrabPool:
    """N worker threads claiming pending items. Own stop event (separate from the
    scanner's). Started from main() in watch modes when the grabber is enabled."""

    def __init__(self, config, db, tagger, notifier=None):
        self.config = config
        self.db = db
        self.pipeline = GrabPipeline(config, db, tagger, notifier=notifier)
        self.n = max(1, min(3, int(config.get("grab_workers", 1))))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self):
        # Startup recovery: return in-flight rows to pending, wipe the temp area.
        reset = self.db.reset_inflight_grabs()
        if reset:
            log.info("Grab recovery: reset %d in-flight item(s) to pending", reset)
        # One-time cleanup of any unbounded event history from before the per-item cap.
        try:
            pruned = self.db.prune_grab_events()
            if pruned:
                log.info("Grab recovery: pruned %d stale audit event(s)", pruned)
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("Grab event prune failed (continuing): %s", exc)
        shutil.rmtree(self.pipeline.grab_tmp, ignore_errors=True)
        os.makedirs(self.pipeline.grab_tmp, exist_ok=True)
        for i in range(self.n):
            t = threading.Thread(target=self._loop, name=f"GrabWorker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("GrabPool started with %d worker(s)", self.n)

    def _loop(self):
        while not self._stop.is_set():
            item = self.db.claim_next_grab()
            if not item:
                self._stop.wait(2)
                continue
            try:
                self.pipeline.process_item(item)
            except Exception as exc:  # belt and suspenders
                log.exception("GrabWorker crashed on item %s: %s", item.get("id"), exc)
            time.sleep(0)  # yield

    def stop(self):
        self._stop.set()

    def join(self, timeout: float = 5.0):
        """Wait up to `timeout` seconds total for workers to finish, after stop()."""
        end = time.monotonic() + timeout
        for t in self._threads:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            t.join(remaining)
