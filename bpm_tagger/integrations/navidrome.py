"""Navidrome library rescan trigger (Subsonic startScan)."""

import hashlib
import logging
import secrets

import requests

log = logging.getLogger(__name__)


def ping_navidrome(url: str, user: str, pwd: str) -> tuple[bool, str]:
    """Subsonic /rest/ping connection test. Returns (ok, message)."""
    if not (url and user and pwd):
        return False, "URL, username and password are required"
    try:
        salt = secrets.token_hex(6)
        token = hashlib.md5((pwd + salt).encode()).hexdigest()
        resp = requests.get(
            f"{url.rstrip('/')}/rest/ping",
            params={"u": user, "t": token, "s": salt, "v": "1.8.0", "c": "bpm-tagger", "f": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        sub = (resp.json() or {}).get("subsonic-response", {})
        if sub.get("status") == "ok":
            return True, "Connected"
        err = (sub.get("error") or {}).get("message", "authentication failed")
        return False, err
    except Exception as exc:
        return False, str(exc)


def _trigger_navidrome_rescan(config: dict):
    url  = config.get("navidrome_url", "").rstrip("/")
    user = config.get("navidrome_user", "")
    pwd  = config.get("navidrome_pass", "")
    if not (url and user and pwd):
        return
    try:
        salt = secrets.token_hex(6)
        token = hashlib.md5((pwd + salt).encode()).hexdigest()
        resp = requests.get(
            f"{url}/rest/startScan",
            params={"u": user, "t": token, "s": salt, "v": "1.8.0", "c": "bpm-tagger", "f": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Navidrome rescan triggered")
    except Exception as exc:
        log.warning("Navidrome rescan request failed: %s", exc)
