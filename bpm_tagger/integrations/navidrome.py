"""Navidrome library rescan trigger (Subsonic startScan)."""

import hashlib
import logging
import secrets

import requests

log = logging.getLogger(__name__)


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
