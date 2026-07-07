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

from .base import DownloadedFile, Provider, ProviderCandidate, ProgressCb, TrackMeta

log = logging.getLogger(__name__)

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
        query = f"{meta.artist} {meta.title}".strip()
        if not query:
            return []
        try:
            return asyncio.run(self._search(query, limit))
        except Exception as exc:
            log.warning("Deezer search failed: %s", exc)
            return []

    async def _search(self, query: str, limit: int) -> list[ProviderCandidate]:
        client = self._make_client()
        await client.login()
        try:
            results = await client.search("track", query, limit=limit)
        finally:
            await self._close(client)
        items = results[0].get("data", []) if results else []
        out: list[ProviderCandidate] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            album = it.get("album") or {}
            dur = it.get("duration")
            out.append(ProviderCandidate(
                provider=self.name,
                provider_track_id=str(it.get("id", "")),
                title=it.get("title", ""),
                artist=(it.get("artist") or {}).get("name", ""),
                album=album.get("title", "") if isinstance(album, dict) else "",
                duration_ms=int(dur * 1000) if isinstance(dur, (int, float)) else None,
                isrc=it.get("isrc", "") or "",
                quality=_quality_name(self.quality),
                cover_url=(album.get("cover_xl") or album.get("cover_big")
                           or album.get("cover_medium") or "") if isinstance(album, dict) else "",
            ))
        return out

    # ── download ───────────────────────────────────────────────────────────────
    def download(self, cand: ProviderCandidate, dest_dir: str,
                 progress_cb: Optional[ProgressCb] = None) -> DownloadedFile:
        os.makedirs(dest_dir, exist_ok=True)
        return asyncio.run(self._download(cand, dest_dir, progress_cb))

    async def _download(self, cand: ProviderCandidate, dest_dir: str,
                        progress_cb: Optional[ProgressCb]) -> DownloadedFile:
        client = self._make_client()
        await client.login()
        try:
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
    def healthcheck(self) -> bool:
        if not self.arl:
            return False
        try:
            return asyncio.run(self._healthcheck())
        except Exception:
            return False

    async def _healthcheck(self) -> bool:
        client = self._make_client()
        await client.login()
        await self._close(client)
        return bool(getattr(client, "logged_in", False))
