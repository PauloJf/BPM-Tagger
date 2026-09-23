"""Neutral hooks the core fires for optional downstream layers.

The scanner and watcher call these instead of importing integrations directly,
so the core never loads (or depends on the packages of) a layer that isn't
configured. Each downstream imports lazily behind its own configuration check.
"""


def library_changed(config: dict, full: bool = False) -> None:
    """Files under MUSIC_DIR changed (tags written). Tell any configured
    downstream server to rescan; ``full`` asks it to re-read every file.
    A no-op when nothing is configured."""
    if (config.get("navidrome_url") and config.get("navidrome_user")
            and config.get("navidrome_pass")):
        from .integrations.navidrome import _trigger_navidrome_rescan
        _trigger_navidrome_rescan(config, full=full)
