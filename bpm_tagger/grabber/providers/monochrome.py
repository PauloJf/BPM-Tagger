"""MonochromeProvider — self-hosted Tidal proxy (§5).

Instance APIs vary, so the endpoint shapes + response parsing are isolated here
as the single place to adjust for a given deployment. Streaming download with
Content-Length progress, retries with backoff on 5xx/timeout, Retry-After on
429, and a circuit breaker that skips the provider for 10 min after 5
consecutive failures.
"""

import logging
import os
import time
from typing import Optional

import requests

from .base import DownloadedFile, Provider, ProviderCandidate, ProgressCb, TrackMeta

log = logging.getLogger(__name__)

# ── Instance-dependent endpoint shapes — adjust here for your Monochrome build ──
SEARCH_PATH = "/search"          # GET {base}/search?s=<query>
TRACK_PATH = "/track/"           # GET {base}/track/?id=<id>&quality=<q>
# Quality ladder, best → acceptable.
QUALITY_LADDER = ["LOSSLESS", "HIGH", "LOW"]

_MAX_RETRIES = 3
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN = 600  # seconds


class MonochromeProvider(Provider):
    name = "monochrome"
    lossless = True

    def __init__(self, config: dict):
        self.base_url = str(config.get("monochrome_base_url", "")).rstrip("/")
        self.api_key = config.get("monochrome_api_key", "")
        self.quality = config.get("monochrome_quality", "LOSSLESS") or "LOSSLESS"
        self.session = requests.Session()
        self._fail_count = 0
        self._breaker_until = 0.0

    # ── circuit breaker ────────────────────────────────────────────────────────
    def _breaker_open(self) -> bool:
        return time.time() < self._breaker_until

    def _record_failure(self):
        self._fail_count += 1
        if self._fail_count >= _BREAKER_THRESHOLD:
            self._breaker_until = time.time() + _BREAKER_COOLDOWN
            log.warning("Monochrome circuit breaker OPEN for %d s after %d failures",
                        _BREAKER_COOLDOWN, self._fail_count)
            self._fail_count = 0

    def _record_success(self):
        self._fail_count = 0

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # ── request with retry/backoff ─────────────────────────────────────────────
    def _get(self, path: str, params: dict = None, stream: bool = False):
        url = f"{self.base_url}{path}"
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, headers=self._headers(),
                                        stream=stream, timeout=(10, 60))
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", "5"))
                    time.sleep(min(wait, 30))
                    continue
                if resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    continue
                resp.raise_for_status()
                return resp
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Monochrome GET {path} failed: {last_exc}")

    # ── search ─────────────────────────────────────────────────────────────────
    def search(self, meta: TrackMeta, limit: int = 8) -> list[ProviderCandidate]:
        if self._breaker_open():
            return []
        query = f"{meta.artist} {meta.title}".strip()
        try:
            resp = self._get(SEARCH_PATH, {"s": query})
            data = resp.json()
        except Exception as exc:
            log.warning("Monochrome search failed: %s", exc)
            self._record_failure()
            return []
        self._record_success()
        return self._parse_search(data, limit)

    def _parse_search(self, data, limit: int) -> list[ProviderCandidate]:
        # Tolerate a few common response shapes: {items:[...]}, {tracks:{items:[...]}}, [...]
        items = []
        if isinstance(data, dict):
            items = (data.get("items") or (data.get("tracks") or {}).get("items")
                     or data.get("results") or [])
        elif isinstance(data, list):
            items = data
        out: list[ProviderCandidate] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            artists = it.get("artists") or []
            artist = (", ".join(a.get("name", "") for a in artists) if artists
                      else it.get("artist") or (it.get("artist") or {}).get("name", "")
                      if isinstance(it.get("artist"), dict) else it.get("artist", ""))
            album = it.get("album")
            album_name = album.get("title") if isinstance(album, dict) else (album or "")
            dur = it.get("duration")
            out.append(ProviderCandidate(
                provider=self.name,
                provider_track_id=str(it.get("id", "")),
                title=it.get("title", ""),
                artist=artist if isinstance(artist, str) else "",
                album=album_name,
                duration_ms=int(dur * 1000) if isinstance(dur, (int, float)) else it.get("duration_ms"),
                isrc=it.get("isrc", "") or "",
                quality=it.get("audioQuality") or it.get("quality") or "",
                url="",
                cover_url=it.get("cover") or "",
            ))
        return out

    # ── download ───────────────────────────────────────────────────────────────
    def download(self, cand: ProviderCandidate, dest_dir: str,
                 progress_cb: Optional[ProgressCb] = None) -> DownloadedFile:
        if self._breaker_open():
            raise RuntimeError("Monochrome circuit breaker open")
        os.makedirs(dest_dir, exist_ok=True)
        try:
            resp = self._get(TRACK_PATH, {"id": cand.provider_track_id, "quality": self.quality},
                             stream=True)
            ext = self._ext_from_response(resp)
            dest = os.path.join(dest_dir, f"mono_{cand.provider_track_id}.{ext}")
            total = int(resp.headers.get("Content-Length", "0"))
            done = 0
            last_report = 0.0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb and total and (time.time() - last_report) >= 1.0:
                        progress_cb(min(1.0, done / total))
                        last_report = time.time()
            if progress_cb:
                progress_cb(1.0)
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return DownloadedFile(path=dest, ext=ext, provider=self.name, quality=self.quality)

    @staticmethod
    def _ext_from_response(resp) -> str:
        ctype = resp.headers.get("Content-Type", "").lower()
        if "flac" in ctype:
            return "flac"
        if "mp4" in ctype or "m4a" in ctype or "aac" in ctype:
            return "m4a"
        if "mpeg" in ctype or "mp3" in ctype:
            return "mp3"
        return "flac"  # Monochrome default is lossless

    def healthcheck(self) -> bool:
        if not self.base_url:
            return False
        try:
            self._get(SEARCH_PATH, {"s": "test"})
            return True
        except Exception:
            return False
