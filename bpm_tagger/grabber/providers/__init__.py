"""Download providers (§5): Deezer (streamrip) + yt-dlp fallback.

Monochrome (Tidal proxy) is on hold — see MONOCHROME_ON_HOLD below.
``build_providers(config, db)`` returns the enabled providers in PROVIDER_ORDER.
"""

import logging

from .base import DownloadedFile, Provider, ProviderCandidate, TrackMeta
from .deezer import DeezerProvider
from .monochrome import MonochromeProvider
from .ytdlp import YtDlpProvider

log = logging.getLogger(__name__)

__all__ = ["Provider", "TrackMeta", "ProviderCandidate", "DownloadedFile",
           "DeezerProvider", "MonochromeProvider", "YtDlpProvider", "build_providers"]

# Monochrome is ON HOLD pending investigation: self-hosted builds need a Tidal
# account, and the public instances (e.g. monochrome.tf) use an API shape the
# provider isn't yet aligned to. Skipped in build_providers regardless of
# PROVIDER_ORDER / MONOCHROME_BASE_URL. Flip to False to re-enable.
MONOCHROME_ON_HOLD = True


def build_providers(config: dict) -> list[Provider]:
    order = [p.strip().lower() for p in str(config.get("provider_order", "deezer,ytdlp")).split(",") if p.strip()]
    built: list[Provider] = []
    for name in order:
        if name == "deezer":
            if config.get("deezer_arl"):
                built.append(DeezerProvider(config))
            else:
                log.info("Provider 'deezer' skipped (DEEZER_ARL not set)")
        elif name == "monochrome":
            if MONOCHROME_ON_HOLD:
                log.info("Provider 'monochrome' is on hold — skipped (MONOCHROME_ON_HOLD)")
                continue
            if config.get("monochrome_base_url"):
                built.append(MonochromeProvider(config))
            else:
                log.info("Provider 'monochrome' skipped (MONOCHROME_BASE_URL not set)")
        elif name == "ytdlp":
            built.append(YtDlpProvider(config))
        else:
            log.warning("Unknown provider in PROVIDER_ORDER: %s", name)
    return built
