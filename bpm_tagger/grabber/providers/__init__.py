"""Download providers (§5): Monochrome (Tidal proxy) + yt-dlp fallback.

``build_providers(config, db)`` returns the enabled providers in PROVIDER_ORDER.
"""

import logging

from .base import DownloadedFile, Provider, ProviderCandidate, TrackMeta
from .monochrome import MonochromeProvider
from .ytdlp import YtDlpProvider

log = logging.getLogger(__name__)

__all__ = ["Provider", "TrackMeta", "ProviderCandidate", "DownloadedFile",
           "MonochromeProvider", "YtDlpProvider", "build_providers"]


def build_providers(config: dict) -> list[Provider]:
    order = [p.strip().lower() for p in str(config.get("provider_order", "monochrome,ytdlp")).split(",") if p.strip()]
    built: list[Provider] = []
    for name in order:
        if name == "monochrome":
            if config.get("monochrome_base_url"):
                built.append(MonochromeProvider(config))
            else:
                log.info("Provider 'monochrome' skipped (MONOCHROME_BASE_URL not set)")
        elif name == "ytdlp":
            built.append(YtDlpProvider(config))
        else:
            log.warning("Unknown provider in PROVIDER_ORDER: %s", name)
    return built
