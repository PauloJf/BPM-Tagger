"""Subsonic request authentication.

Credentials are **separate from the web login** (docs/plans/subsonic-api.md):
the web password stays hashed and never lands in a client config. Per account:

* an OpenSubsonic **API key** (``apiKey=``), stored as a sha256 hash;
* a generated **Subsonic password** for classic clients. Token auth
  (``t = md5(password + s)``) needs the server to know the plaintext, so it is
  stored as-is — the same posture as the Navidrome password in settings.json.
  It is random, API-only and revocable, so a leak can stream music, not reach
  the admin UI.

Plaintext ``p=`` (incl. ``enc:``) is only accepted over https, from a private
network, or when ``subsonic_allow_plain_password`` is on.

Failures feed the same per-IP / per-account / global lockout as ``/api/login``.

Accounts: the admin (whole library) and player users (Phase 2). A player sees
only the tracks of the playlists it is associated with — the same rule as Run
mode — and can't create or edit playlists. Disabling or deleting a player
locks its Subsonic access out on the very next request.
"""

import hashlib
import hmac
import ipaddress
import threading
import time
from dataclasses import dataclass
from typing import Optional

from flask import request

from ..auth import admin_username
from .envelope import (E_AUTH_MECHANISM_UNSUPPORTED, E_CONFLICTING_AUTH, E_INVALID_API_KEY,
                       E_MISSING_PARAM, E_NOT_AUTHORIZED, E_WRONG_CREDENTIALS, SubsonicError)

ADMIN_OWNER = "admin"

_touch_lock = threading.Lock()
_last_touch: dict[str, float] = {}
_TOUCH_EVERY = 60.0  # seconds — last_used_at, without a DB write per request


@dataclass(frozen=True)
class Principal:
    owner: str                      # 'admin' | 'player:<id>'
    username: str
    scope: Optional[list] = None    # None = whole library; else allowed playlist ids

    @property
    def is_admin(self) -> bool:
        return self.owner == ADMIN_OWNER


def player_owner(player_id: int) -> str:
    return f"player:{player_id}"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def subsonic_username() -> str:
    """The admin's Subsonic username: the admin username when one is set, else "admin"."""
    return admin_username() or "admin"


def _owner_for_username(db, username: str):
    if username.strip().lower() == subsonic_username().lower():
        return ADMIN_OWNER
    row = db.get_player_by_username(username.strip().lower())
    return player_owner(row["id"]) if row else None


def principal_for(db, owner: str) -> Optional[Principal]:
    """The live account behind ``owner``, or None if it no longer may sign in."""
    if owner == ADMIN_OWNER:
        return Principal(ADMIN_OWNER, subsonic_username(), None)
    if owner.startswith("player:") and owner[7:].isdigit():
        row = db.get_player(int(owner[7:]))
        if row and row.get("enabled"):
            return Principal(owner, row["username"],
                             sorted(db.playlist_ids_for_player(row["id"])))
    return None


def _decode_plain(p: str) -> str:
    if p.startswith("enc:"):
        try:
            return bytes.fromhex(p[4:]).decode("utf-8")
        except ValueError:
            return ""
    return p


def plain_password_allowed(config: dict) -> bool:
    if config.get("subsonic_allow_plain_password"):
        return True
    if request.is_secure or str(config.get("ui_public_url", "")).lower().startswith("https://"):
        return True
    try:
        addr = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def _touch(db, owner: str) -> None:
    now = time.monotonic()
    with _touch_lock:
        if now - _last_touch.get(owner, 0.0) < _TOUCH_EVERY:
            return
        _last_touch[owner] = now
    try:
        db.touch_subsonic_credentials(owner)
    except Exception:  # pragma: no cover - bookkeeping only
        pass


def authenticate(st) -> Principal:
    """Resolve the request to an account, or raise SubsonicError."""
    v = request.values
    user = (v.get("u") or "").strip()
    token, salt, plain, key = v.get("t") or "", v.get("s") or "", v.get("p") or "", v.get("apiKey") or ""
    ip = request.remote_addr or "unknown"

    if key and (user or token or plain):
        raise SubsonicError(E_CONFLICTING_AUTH, "Provide either apiKey or u + t/s or p, not both.")
    if not key and not user:
        raise SubsonicError(E_MISSING_PARAM, "Required parameter is missing: u (or apiKey).")
    if user and not ((token and salt) or plain):
        raise SubsonicError(E_MISSING_PARAM, "Required parameter is missing: t and s, or p.")
    if plain and not key and not (token and salt) and not plain_password_allowed(st.config):
        raise SubsonicError(E_AUTH_MECHANISM_UNSUPPORTED,
                            "Plaintext passwords are only accepted over https or from a "
                            "private network. Use token authentication or an API key.")

    account = "subsonic:apikey" if key else f"subsonic:{user.lower()}"
    now = time.time()
    with st.login_lock:
        if st.login_locked(ip, account, now):
            raise SubsonicError(E_WRONG_CREDENTIALS, "Too many failed attempts. Try again later.")

        owner = None
        if key:
            owner = st.db.find_subsonic_owner_by_key_hash(hash_api_key(key))
        else:
            candidate = _owner_for_username(st.db, user)
            cred = st.db.get_subsonic_credentials(candidate) if candidate else None
            stored = (cred or {}).get("password") or ""
            if stored:
                if token and salt:
                    expected = hashlib.md5((stored + salt).encode("utf-8")).hexdigest()
                    good = hmac.compare_digest(expected, token.lower())
                else:
                    good = hmac.compare_digest(_decode_plain(plain).encode(), stored.encode())
                owner = candidate if good else None

        if owner is None:
            st.login_failed(ip, account, now)
            if key:
                raise SubsonicError(E_INVALID_API_KEY, "Invalid API key.")
            raise SubsonicError(E_WRONG_CREDENTIALS, "Wrong username or password.")
        st.login_succeeded(ip, account)

    who = principal_for(st.db, owner)
    if who is None:
        # Credentials outlived their account (player disabled or deleted).
        raise SubsonicError(E_NOT_AUTHORIZED, "This account has no Subsonic access.")
    _touch(st.db, owner)
    return who
