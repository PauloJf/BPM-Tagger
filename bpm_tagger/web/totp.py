"""RFC 6238 TOTP for the admin second factor — implemented with the stdlib only.

No new dependency: TOTP is just HMAC-SHA1 over a 30-second time counter, which
``hmac``/``hashlib`` already give us. Also holds base32 secret generation, the
``otpauth://`` enrolment URI (for an authenticator app), and one-time recovery
codes. Recovery-code *hashing* is done by the caller with werkzeug (the same
primitive the password hashes use); this module only generates and formats them.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import List, Optional
from urllib.parse import quote

DIGITS = 6
PERIOD = 30          # seconds per TOTP step
DRIFT_STEPS = 1      # accept ±1 step (±30s) for client/server clock skew


def generate_secret() -> str:
    """A fresh 160-bit base32 TOTP secret (unpadded, as authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    """The HOTP value (RFC 4226) for a counter — the building block of TOTP."""
    pad = "=" * (-len(secret_b32) % 8)            # b32decode needs a multiple of 8
    key = base64.b32decode(secret_b32.upper() + pad, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F                     # dynamic truncation
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret_b32: str, code: str, now: Optional[float] = None,
           drift: int = DRIFT_STEPS) -> bool:
    """True if ``code`` matches the current TOTP step (±drift steps). Each
    candidate is compared in constant time. Non-numeric / wrong-length input is
    rejected up front."""
    if not secret_b32 or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    step = int((time.time() if now is None else now) // PERIOD)
    for offset in range(-drift, drift + 1):
        if hmac.compare_digest(_hotp(secret_b32, step + offset), code):
            return True
    return False


def provisioning_uri(secret_b32: str, account: str, issuer: str = "BPM Tagger") -> str:
    """An ``otpauth://`` URI an authenticator app scans (or the secret can be
    typed by hand). ``account`` labels the entry (e.g. the admin's name/host)."""
    label = quote(f"{issuer}:{account}")
    params = (f"secret={secret_b32}&issuer={quote(issuer)}"
              f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")
    return f"otpauth://totp/{label}?{params}"


def generate_recovery_codes(n: int = 10) -> List[str]:
    """``n`` human-typeable one-time codes (``abcde-fghij``). The caller stores
    only their hashes and shows the plaintext to the admin exactly once."""
    codes = []
    for _ in range(n):
        raw = base64.b32encode(secrets.token_bytes(6)).decode("ascii").rstrip("=").lower()
        codes.append(f"{raw[:5]}-{raw[5:10]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    """Canonical form for hashing/matching a recovery code (case- and space-insensitive)."""
    return code.strip().lower().replace(" ", "")
