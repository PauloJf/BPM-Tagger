"""Auth: CSRF helpers, login_required, brute-force lockout, login/logout routes."""

import hmac
import logging
import secrets
import time
from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)

from .state import state

log = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# CSRF + redirect safety helpers
# ---------------------------------------------------------------------------

def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def _check_csrf():
    token = (request.form.get("csrf_token")
             or request.headers.get("X-CSRF-Token", ""))
    stored = session.get("csrf_token", "")
    if not token or not stored or not hmac.compare_digest(token, stored):
        abort(403)


def _is_safe_redirect(url: str) -> bool:
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, url))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("ok"):
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    _csrf_token()  # ensure token exists before rendering
    if request.method == "POST":
        _check_csrf()
        st = state()
        ip = request.remote_addr or "unknown"
        now = time.time()
        with st.login_lock:
            if now < st.login_lockout_until[ip]:
                return render_template("login.html", lockout=True), 429
            attempts = [t for t in st.login_attempts[ip] if now - t < st.attempt_window]
            st.login_attempts[ip] = attempts
            if len(attempts) >= st.max_login_attempts:
                st.login_lockout_until[ip] = now + st.lockout_seconds
                st.login_attempts[ip] = []
                return render_template("login.html", lockout=True), 429
            if request.form.get("password") == current_app.config["UI_PASSWORD"]:
                st.login_attempts.pop(ip, None)
                st.login_lockout_until.pop(ip, None)
                session["ok"] = True
                next_url = request.args.get("next")
                target = next_url if next_url and _is_safe_redirect(next_url) else url_for("pages.tracks")
                return redirect(target)
            st.login_attempts[ip].append(now)
        flash("Wrong password.", "error")
    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    _check_csrf()
    session.clear()
    return redirect(url_for("auth.login"))
