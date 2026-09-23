"""Optional Subsonic / OpenSubsonic API — docs/plans/subsonic-api.md.

Opt-in (``SUBSONIC_ENABLED``, default off). ``web/app.py`` imports this package
and registers the blueprint only when enabled, so a disabled install has no
``/rest`` routes at all. Stateless: no session, no cookie, no CSRF — every
request carries its own credentials (see ``auth.py``).
"""

import logging

from flask import Blueprint

from ..state import state
from .auth import authenticate
from .envelope import E_GENERIC, E_NOT_FOUND, SubsonicError, failed
from .handlers import METHODS

log = logging.getLogger(__name__)

subsonic_bp = Blueprint("subsonic", __name__)

# Stateless and credential-bearing: exempt from session CSRF creation.
ENDPOINTS = ("subsonic.dispatch",)


@subsonic_bp.route("/rest/<method>", methods=["GET", "POST"])
def dispatch(method: str):
    name = method[:-5] if method.endswith(".view") else method
    st = state()
    try:
        who = authenticate(st)
        handler = METHODS.get(name)
        if handler is None:
            return failed(E_NOT_FOUND if not name else E_GENERIC,
                          f"Method '{name}' is not supported by this server.")
        return handler(st, who)
    except SubsonicError as exc:
        return failed(exc.code, exc.message)
    except Exception:
        # Never echo internals (or the credential-bearing query string) back.
        log.exception("Subsonic %s failed", name)
        return failed(E_GENERIC, "Internal server error.")
