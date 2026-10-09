"""DeezerProvider — Deezer downloads via streamrip's DeezerClient (§5).

A free-tier Deezer ARL yields *full-length* tracks (not 30 s previews) at
MP3 128 kbps; MP3_320 and FLAC require a paid subscription and raise on a free
account. streamrip's client is async and wraps the synchronous ``deezer-py``
library plus the Blowfish stream decryption Deezer downloads need. We bridge it
to the sync Provider interface with a fresh event loop per operation, which
keeps it thread-safe across the GrabPool worker threads (each runs its own
loop; a persistent aiohttp session can't cross loops safely).

The ARL is a session credential — sourced from env/config only, never logged.
"""

import asyncio
import logging
import os
from typing import Optional

from .base import (DownloadedFile, Provider, ProviderAuthError, ProviderCandidate,
                   ProgressCb, SearchResults, TrackMeta, exc_text)

log = logging.getLogger(__name__)

ARL_REJECTED = ("Deezer rejected the ARL (expired or invalid) — "
                "paste a fresh one in Settings → Grabber")
ARL_MISSING = "No Deezer ARL configured"

# Human-readable quality → streamrip quality integer.
_QUALITY_MAP = {"MP3_128": 0, "MP3_320": 1, "FLAC": 2}


def _quality_int(name: str) -> int:
    return _QUALITY_MAP.get(str(name or "").upper(), 0)


def _quality_name(q: int) -> str:
    for name, i in _QUALITY_MAP.items():
        if i == q:
            return name
    return "MP3_128"


class DeezerProvider(Provider):
    name = "deezer"
    # Free tier caps at 128 kbps MP3; only meaningfully lossless with a HiFi ARL.
    lossless = False

    def __init__(self, config: dict):
        self.arl = str(config.get("deezer_arl", "") or "")
        self.quality = _quality_int(config.get("deezer_quality", "MP3_128"))
        # Injectable for tests: a callable(arl) -> client. None → real streamrip.
        self._client_factory: Optional[callable] = None

    # ── streamrip client (lazy import so streamrip isn't required until used) ────
    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory(self.arl)
        from streamrip.client.deezer import DeezerClient
        from streamrip.config import Config
        cfg = Config.defaults()
        cfg.session.deezer.arl = self.arl
        return DeezerClient(cfg)

    @staticmethod
    async def _login(client):
        """Log in, turning streamrip's bare (message-less) credential errors into
        a ProviderAuthError with a message the user can act on. Callers wrap this
        in their try/finally: streamrip opens its aiohttp session *before*
        checking the ARL, so a failed login still has a session to close."""
        try:
            await client.login()
        except Exception as exc:
            name = type(exc).__name__
            if name == "AuthenticationError":
                raise ProviderAuthError(ARL_REJECTED) from exc
            if name == "MissingCredentialsError":
                raise ProviderAuthError(ARL_MISSING) from exc
            raise

    @staticmethod
    async def _close(client):
        sess = getattr(client, "session", None)
        if sess is not None:
            try:
                await sess.close()
            except Exception:
                pass

    # ── search ───────────────────────────────────────────────────────────────
    def search(self, meta: TrackMeta, limit: int = 8) -> list[ProviderCandidate]:
        if not self.arl:
            return []
        # ISRC first: an exact id the text search below can miss (feat./remaster/
        # edit suffixes, multi-artist credits). It leads the list, with the text
        # hits behind it as fallbacks if its download fails. Falls through to the
        # text search alone when Deezer has no such ISRC or the track isn't
        # streamable from the account's country.
        hit, note = self._search_isrc(meta.isrc)
        found = [hit] if hit else []
        query = f"{meta.artist} {meta.title}".strip()
        if not query:
            return SearchResults(found, note)
        try:
            text = asyncio.run(self._search(query, limit))
        except ProviderAuthError:
            raise  # not a per-query miss: let the caller surface it
        except Exception as exc:
            log.warning("Deezer search failed: %s", exc_text(exc))
            return SearchResults(found, note)
        return SearchResults(
            found + [c for c in text if not hit or c.provider_track_id != hit.provider_track_id],
            note)

    def _search_isrc(self, isrc: str) -> tuple[Optional[ProviderCandidate], str]:
        """(candidate, note). The note is non-empty only when an ISRC was given but
        yielded no usable Deezer track, saying why the title search ran instead."""
        isrc = (isrc or "").strip().upper()
        if not isrc:
            return None, ""
        from bpm_tagger.integrations import deezer_catalog
        t = deezer_catalog.track_detail_by_isrc(isrc)
        if t is None:
            return None, f"Couldn't reach Deezer's ISRC lookup for {isrc}; searched by title instead"
        if not t:
            return None, f"ISRC {isrc} isn't in Deezer's catalogue; searched by title instead"
        if t.get("readable") is False:
            return None, (f"Deezer lists ISRC {isrc} but it isn't streamable from your account's "
                          "country; searched by title instead")
        return self._candidate(t, isrc), ""

    def _candidate(self, it: dict, isrc: str = "") -> ProviderCandidate:
        album = it.get("album") or {}
        if not isinstance(album, dict):
            album = {}
        dur = it.get("duration")
        return ProviderCandidate(
            provider=self.name,
            provider_track_id=str(it.get("id", "")),
            title=it.get("title", ""),
            artist=(it.get("artist") or {}).get("name", ""),
            album=album.get("title", ""),
            duration_ms=int(dur * 1000) if isinstance(dur, (int, float)) else None,
            isrc=it.get("isrc", "") or isrc,
            quality=_quality_name(self.quality),
            cover_url=(album.get("cover_xl") or album.get("cover_big")
                       or album.get("cover_medium") or ""),
        )

    async def _search(self, query: str, limit: int) -> list[ProviderCandidate]:
        client = self._make_client()
        try:
            await self._login(client)
            results = await client.search("track", query, limit=limit)
        finally:
            await self._close(client)
        items = results[0].get("data", []) if results else []
        return [self._candidate(it) for it in items[:limit] if isinstance(it, dict)]

    # ── download ───────────────────────────────────────────────────────────────
    def download(self, cand: ProviderCandidate, dest_dir: str,
                 progress_cb: Optional[ProgressCb] = None) -> DownloadedFile:
        os.makedirs(dest_dir, exist_ok=True)
        return asyncio.run(self._download(cand, dest_dir, progress_cb))

    async def _download(self, cand: ProviderCandidate, dest_dir: str,
                        progress_cb: Optional[ProgressCb]) -> DownloadedFile:
        client = self._make_client()
        try:
            await self._login(client)
            dl = await client.get_downloadable(cand.provider_track_id, quality=self.quality)
            ext = getattr(dl, "extension", "mp3")
            dest = os.path.join(dest_dir, f"dz_{cand.provider_track_id}.{ext}")
            total = int(getattr(dl, "_size", 0) or 0)
            done = {"n": 0}

            def cb(n):
                done["n"] += n
                if progress_cb and total:
                    progress_cb(min(1.0, done["n"] / total))

            await dl.download(dest, cb)
            if progress_cb:
                progress_cb(1.0)
        finally:
            await self._close(client)
        return DownloadedFile(path=dest, ext=ext, provider=self.name,
                              quality=_quality_name(self.quality))

    # ── health ───────────────────────────────────────────────────────────────
    def verify(self) -> Optional[str]:
        """Try to log in. None when the ARL works, else a user-facing reason."""
        if not self.arl:
            return ARL_MISSING
        try:
            asyncio.run(self._verify())
            return None
        except ProviderAuthError as exc:
            return str(exc)
        except Exception as exc:
            return f"Couldn't reach Deezer: {exc_text(exc)}"

    async def _verify(self) -> None:
        client = self._make_client()
        try:
            await self._login(client)
        finally:
            await self._close(client)
        if not getattr(client, "logged_in", False):
            raise ProviderAuthError(ARL_REJECTED)

    def healthcheck(self) -> bool:
        return self.verify() is None
