"""YtDlpProvider — YouTube (Music) fallback via yt-dlp as a library (§5).

Searches YouTube Music first, then falls back to a plain ytsearch. Downloads
bestaudio with no postprocessors (our ffmpeg step does the transcode). yt-dlp is
imported lazily so it isn't required until actually used (and so tests can inject
a fake YoutubeDL).
"""

import logging
import os
from typing import Optional
from urllib.parse import quote

from .base import DownloadedFile, Provider, ProviderCandidate, ProgressCb, TrackMeta

log = logging.getLogger(__name__)


class YtDlpProvider(Provider):
    name = "ytdlp"
    lossless = False

    def __init__(self, config: dict):
        self._ydl_cls = None
        self.version = None

    def _YoutubeDL(self):
        if self._ydl_cls is None:
            import yt_dlp
            self._ydl_cls = yt_dlp.YoutubeDL
            self.version = getattr(getattr(yt_dlp, "version", None), "__version__", None)
        return self._ydl_cls

    # ── search ─────────────────────────────────────────────────────────────────
    def search(self, meta: TrackMeta, limit: int = 8) -> list[ProviderCandidate]:
        query = f"{meta.artist} {meta.title}".strip()
        entries = self._search_entries(query, limit)
        out: list[ProviderCandidate] = []
        for e in entries[:limit]:
            if not isinstance(e, dict):
                continue
            channel = e.get("uploader") or e.get("channel") or ""
            is_topic = channel.strip().endswith("- Topic")
            artist = channel[:-len("- Topic")].strip() if is_topic else channel
            dur = e.get("duration")
            out.append(ProviderCandidate(
                provider=self.name,
                provider_track_id=str(e.get("id", "")),
                title=e.get("title", ""),
                artist=artist,
                album=e.get("album", "") or "",
                duration_ms=int(dur * 1000) if isinstance(dur, (int, float)) else None,
                quality="bestaudio",
                url=e.get("url") or e.get("webpage_url") or "",
                channel=channel,
                is_topic=is_topic,
            ))
        return out

    def _search_entries(self, query: str, limit: int) -> list:
        try:
            YDL = self._YoutubeDL()
        except Exception as exc:
            log.warning("yt-dlp not available: %s", exc)
            return []
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
                "skip_download": True, "noplaylist": True}
        targets = [
            f"https://music.youtube.com/search?q={quote(query)}",
            f'ytsearch{limit}:"{query}" audio',
        ]
        for target in targets:
            try:
                with YDL(opts) as ydl:
                    info = ydl.extract_info(target, download=False)
                entries = info.get("entries") if isinstance(info, dict) else None
                if entries:
                    return list(entries)
            except Exception as exc:
                log.debug("yt-dlp search '%s' failed: %s", target, exc)
        return []

    # ── download ───────────────────────────────────────────────────────────────
    def download(self, cand: ProviderCandidate, dest_dir: str,
                 progress_cb: Optional[ProgressCb] = None) -> DownloadedFile:
        YDL = self._YoutubeDL()
        os.makedirs(dest_dir, exist_ok=True)
        outtmpl = os.path.join(dest_dir, f"yt_{cand.provider_track_id}.%(ext)s")
        captured = {}

        def hook(d):
            if d.get("status") == "downloading" and progress_cb:
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    progress_cb(min(1.0, d.get("downloaded_bytes", 0) / total))
            elif d.get("status") == "finished":
                captured["filename"] = d.get("filename")

        opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best",
                "outtmpl": outtmpl, "noplaylist": True, "progress_hooks": [hook],
                "postprocessors": []}
        url = cand.url or f"https://music.youtube.com/watch?v={cand.provider_track_id}"
        with YDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = captured.get("filename") or ydl.prepare_filename(info)
        if progress_cb:
            progress_cb(1.0)
        ext = os.path.splitext(filename)[1].lstrip(".")
        return DownloadedFile(path=filename, ext=ext, provider=self.name, quality="bestaudio")

    def healthcheck(self) -> bool:
        try:
            self._YoutubeDL()
            return True
        except Exception:
            return False
