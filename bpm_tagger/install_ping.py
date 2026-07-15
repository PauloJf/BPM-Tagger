"""Optional, opt-in, one-time anonymous install ping.

What it is: a single "courtesy ping" so the author can gauge roughly how many
installs of BPM Tagger exist. Nothing more.

Privacy — what is and isn't sent:
  • Sent: the app version, once, as a GoatCounter path (``/install/<version>``).
  • NOT sent: any identifier, your library, file paths, usage, or settings.
  • No cookies. GoatCounter (the receiver) does not store IP addresses.

It is off by default and never fires unless the user explicitly opts in (a
first-run prompt in the web UI, or the ``INSTALL_PING=true`` env var for headless
installs). Once opted in it fires once per version — on the first opt-in and
again after each update — so upgrades are counted; it never fires again for a
version already pinged. Declining changes nothing. The whole mechanism is this
one small, auditable file.
"""

import logging
import threading

import requests

from .config import __version__, save_settings

log = logging.getLogger(__name__)

_TIMEOUT = 8


# Presented as a browser-like client so GoatCounter records the beacon in the
# normal count instead of bucketing this server-side request as bot traffic
# (which its dashboard hides by default). The app + version still travel in the
# path (/install/<version>), so nothing about what's sent is obscured.
_USER_AGENT = "Mozilla/5.0 (compatible; BPM-Tagger install ping)"


def _send(url: str, version: str) -> bool:
    """Fire the ping at GoatCounter's pixel endpoint. Returns True on a 2xx
    response, False on anything else. The version rides in the counted path."""
    try:
        resp = requests.get(
            url,
            params={"p": f"/install/{version}"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
        return resp.ok
    except Exception as exc:  # network down, DNS, timeout — never surface
        log.debug("Install ping failed (ignored): %s", exc)
        return False


def _should_ping(config: dict, version: str) -> bool:
    """True when the user opted in, a URL is configured, and this version hasn't
    been pinged yet — i.e. on the first opt-in and once after each update."""
    return (config.get("install_ping_consent") is True
            and bool(str(config.get("install_ping_url") or "").strip())
            and config.get("install_ping_version") != version)


def maybe_send_install_ping(config: dict, settings_path: str) -> None:
    """Send the install ping in the background if this version hasn't pinged yet.

    No-op unless the user opted in, a URL is configured, and the running version
    differs from the last one pinged (so it fires on the first opt-in and again
    after each update). Runs on a daemon thread and never blocks startup;
    failures are swallowed and retried on the next start (the pinged version is
    persisted only on success, so a delivered ping never repeats for that
    version)."""
    if not _should_ping(config, __version__):
        return
    url = str(config.get("install_ping_url") or "").strip()

    def _run() -> None:
        if not _send(url, __version__):
            return
        config["install_ping_version"] = __version__
        try:
            save_settings(settings_path, {"install_ping_version": __version__})
        except Exception as exc:  # pragma: no cover - best effort
            log.debug("Could not persist install_ping_version: %s", exc)
        log.info("Sent anonymous install ping (version %s)", __version__)

    threading.Thread(target=_run, daemon=True, name="install-ping").start()
