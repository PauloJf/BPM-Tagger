"""Ecosystem identity tokens: short-lived, HMAC-signed, stdlib only.

One app mints a token and another verifies it with the shared ``ECOSYSTEM_SECRET``. The wire
format and the checks are the contract in ``identity-token-v1.json``; this file is kept
byte-identical in every companion app and is pinned by that contract's test vectors.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

VERSION = "v1"
MIN_SECRET_LENGTH = 32
DEFAULT_TTL = 60
MAX_TTL = 120
CLOCK_SKEW = 60
FIELDS = ("iss", "aud", "sub", "username", "role", "iat", "exp", "jti")


class TokenError(Exception):
    """Why a token was refused. ``code`` is safe to log; the token itself never is."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def usable_secret(secret) -> bool:
    return isinstance(secret, str) and len(secret) >= MIN_SECRET_LENGTH


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, signed_part: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), signed_part.encode("ascii"), hashlib.sha256).digest())


def mint(secret: str, *, iss: str, aud: str, sub: str, username: str, role: str,
         ttl: int = DEFAULT_TTL, now: float | None = None) -> str:
    if not usable_secret(secret):
        raise TokenError("no_secret")
    iat = int(time.time() if now is None else now)
    payload = {"iss": iss, "aud": aud, "sub": sub, "username": username, "role": role,
               "iat": iat, "exp": iat + max(1, min(int(ttl), MAX_TTL)), "jti": secrets.token_hex(8)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{VERSION}.{body}.{_sign(secret, f'{VERSION}.{body}')}"


class SeenTokens:
    """Single use: remembers each accepted ``jti`` until it would have expired anyway."""

    def __init__(self):
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def first_use(self, jti: str, exp: int, now: float) -> bool:
        with self._lock:
            for old in [k for k, e in self._seen.items() if e <= now]:
                del self._seen[old]
            if jti in self._seen:
                return False
            self._seen[jti] = exp
            return True


def verify(secret: str, token: str, *, aud: str, seen: SeenTokens | None = None,
           now: float | None = None) -> dict:
    """The payload of a token that is authentic, for us, current and unused. Else ``TokenError``."""
    if not usable_secret(secret):
        raise TokenError("no_secret")
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or parts[0] != VERSION or not parts[1] or not parts[2]:
        raise TokenError("malformed")
    if not hmac.compare_digest(_sign(secret, f"{parts[0]}.{parts[1]}"), parts[2]):
        raise TokenError("bad_signature")
    try:
        payload = json.loads(_unb64(parts[1]))
    except (ValueError, UnicodeDecodeError):
        raise TokenError("malformed") from None
    if not isinstance(payload, dict) or set(payload) != set(FIELDS):
        raise TokenError("malformed")
    if not all(isinstance(payload[k], str) and payload[k] for k in FIELDS[:5] + ("jti",)):
        raise TokenError("malformed")
    iat, exp = payload["iat"], payload["exp"]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (iat, exp)):
        raise TokenError("malformed")
    if payload["aud"] != aud:
        raise TokenError("wrong_audience")
    current = time.time() if now is None else now
    if exp <= current or iat > current + CLOCK_SKEW or not 0 < exp - iat <= MAX_TTL:
        raise TokenError("expired")
    if seen is not None and not seen.first_use(payload["jti"], exp, current):
        raise TokenError("replayed")
    return payload
