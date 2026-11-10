"""Ecosystem identity: this app mints short-lived signed tokens for companion apps.

Off without ECOSYSTEM_SECRET. Admin and members get a token; kiosk players and the
shared guest never do. The secret stays on the server and out of settings.json."""

import hashlib
import json
import logging
from pathlib import Path

import contract_schema as cs
import pytest
from conftest import _make_app

from bpm_tagger import config as config_mod
from bpm_tagger.web import ecosystem_token as et

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "docs" / "contracts" / "identity-token-v1.json"
CONTRACT_DIGEST = "d77f2a805b0fd161f495cf7a24f12171c79dbad5363a9a3d39b86ded7ea75ab3"
# The token module is kept byte-identical in every companion app.
TOKEN_MODULE_SHA256 = "1209d8fbd77e357932567f63756d38678215e269fc276c7e6b0efe3dd0cc4182"
SECRET = "unit-test-ecosystem-secret-0123456789abcdef"
URL = "/api/ecosystem/token?aud=music-grabber"


@pytest.fixture(scope="module")
def contract():
    return cs.load(CONTRACT_PATH)


@pytest.fixture
def app(base_config):
    base_config["ecosystem_secret"] = SECRET
    return _make_app(base_config)


def _admin(app):
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


def _account(app, admin, csrf, name, role):
    r = admin.post("/api/players", json={"username": name, "password": "longpass1", "role": role}, headers=csrf)
    c = app.test_client()
    assert c.post("/api/login", json={"username": name, "password": "longpass1"}).status_code == 200
    return c, r.get_json()["player"]["id"]


# ── the token format ──────────────────────────────────────────────────────────
def test_contract_and_token_module_are_the_pinned_ones(contract):
    assert cs.digest(CONTRACT_PATH) == CONTRACT_DIGEST
    module = ROOT / "bpm_tagger" / "web" / "ecosystem_token.py"
    assert hashlib.sha256(module.read_bytes()).hexdigest() == TOKEN_MODULE_SHA256
    for name, examples in contract["examples"].items():
        for example in examples:
            cs.check(example, name, contract)


def test_contract_vectors(contract):
    v = contract["vectors"]
    for case in v["valid"]:
        p = case["payload"]
        assert et.verify(v["secret"], case["token"], aud=p["aud"], now=p["iat"] + 1) == p
    for case in v["invalid"]:
        with pytest.raises(et.TokenError):
            et.verify(v["secret"], case["token"], aud="music-grabber", now=1794000001)


def test_verify_refuses_wrong_audience_expiry_replay_and_long_lifetimes():
    tok = et.mint(SECRET, iss="bpm-tagger", aud="music-grabber", sub="admin", username="admin", role="admin", now=1000)
    assert et.verify(SECRET, tok, aud="music-grabber", now=1001)["sub"] == "admin"
    for kwargs, code in (({"aud": "bpm-tagger", "now": 1001}, "wrong_audience"),
                         ({"aud": "music-grabber", "now": 1060}, "expired"),
                         ({"aud": "music-grabber", "now": 900}, "expired")):      # issued in the future
        with pytest.raises(et.TokenError) as e:
            et.verify(SECRET, tok, **kwargs)
        assert e.value.code == code
    seen = et.SeenTokens()
    et.verify(SECRET, tok, aud="music-grabber", seen=seen, now=1001)
    with pytest.raises(et.TokenError) as e:
        et.verify(SECRET, tok, aud="music-grabber", seen=seen, now=1002)
    assert e.value.code == "replayed"
    with pytest.raises(et.TokenError):
        et.verify("another-secret-of-sufficient-length-000000", tok, aud="music-grabber", now=1001)
    assert et.mint(SECRET, iss="a", aud="b", sub="c", username="d", role="admin", ttl=9999, now=0)  # ttl is capped
    capped = et.mint(SECRET, iss="a", aud="b", sub="c", username="d", role="admin", ttl=9999, now=1000)
    assert et.verify(SECRET, capped, aud="b", now=1001)["exp"] == 1000 + et.MAX_TTL
    with pytest.raises(et.TokenError):
        et.mint("short", iss="a", aud="b", sub="c", username="d", role="admin")


# ── the endpoint ──────────────────────────────────────────────────────────────
def test_off_without_a_secret_or_with_a_short_one(base_config):
    for value in (None, "", "too-short"):
        cfg = dict(base_config)
        if value is not None:
            cfg["ecosystem_secret"] = value
        admin, _ = _admin(_make_app(cfg))
        assert admin.get(URL).status_code == 404


def test_needs_a_session(app):
    assert app.test_client().get(URL).status_code == 401


def test_admin_gets_a_token_that_matches_the_contract(app, contract):
    admin, _ = _admin(app)
    r = admin.get(URL)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    cs.check(r.get_json(), "Issued", contract)
    payload = et.verify(SECRET, r.get_json()["token"], aud="music-grabber")
    cs.check(payload, "Payload", contract)
    assert (payload["iss"], payload["sub"], payload["role"]) == ("bpm-tagger", "admin", "admin")
    assert payload["exp"] - payload["iat"] == 60
    assert admin.get(URL).get_json()["token"] != r.get_json()["token"]          # a fresh jti each time


def test_member_gets_a_token_with_a_stable_subject(app, contract):
    admin, csrf = _admin(app)
    member, pid = _account(app, admin, csrf, "Ana", "member")
    payload = et.verify(SECRET, member.get(URL).get_json()["token"], aud="music-grabber")
    cs.check(payload, "Payload", contract)
    assert (payload["sub"], payload["username"], payload["role"]) == (f"player:{pid}", "ana", "member")


def test_kiosk_players_and_the_guest_never_get_a_token(app, base_config):
    admin, csrf = _admin(app)
    player, _ = _account(app, admin, csrf, "kiosk", "player")
    assert player.get(URL).status_code == 403
    cfg = dict(base_config, run_password="guestpass")
    guest_app = _make_app(cfg)
    guest = guest_app.test_client()
    assert guest.post("/api/login", json={"password": "guestpass"}).status_code == 200
    assert guest.get(URL).status_code == 403


def test_a_disabled_member_loses_the_token_at_once(app):
    admin, csrf = _admin(app)
    member, pid = _account(app, admin, csrf, "ana", "member")
    assert member.get(URL).status_code == 200
    assert admin.patch(f"/api/players/{pid}", json={"enabled": False}, headers=csrf).status_code == 200
    assert member.get(URL).status_code == 401


def test_unknown_audience_is_refused(app):
    admin, _ = _admin(app)
    assert admin.get("/api/ecosystem/token?aud=somewhere-else").status_code == 400
    assert admin.get("/api/ecosystem/token").status_code == 400


# ── the secret stays put ──────────────────────────────────────────────────────
def test_the_secret_is_masked_in_settings_never_logged_and_not_settable(app, caplog):
    admin, csrf = _admin(app)
    with caplog.at_level(logging.DEBUG):
        token = admin.get(URL).get_json()["token"]
        body = admin.get("/api/settings").get_data(as_text=True)
    assert SECRET not in body and json.loads(body)["settings"]["ecosystem_secret"] == "********"
    assert SECRET not in caplog.text and token not in caplog.text
    admin.post("/api/settings", json={"ecosystem_secret": "x" * 40}, headers=csrf)
    assert et.verify(SECRET, admin.get(URL).get_json()["token"], aud="music-grabber")   # still the env one
    settings_file = Path(config_mod.settings_file_path(app.extensions["state"].config["db_path"]))
    assert not settings_file.exists() or "ecosystem_secret" not in settings_file.read_text()
    for path in ("/api/me", "/healthz"):
        assert SECRET not in admin.get(path).get_data(as_text=True)


def test_settings_json_cannot_supply_the_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("ECOSYSTEM_SECRET", raising=False)
    db_path = str(tmp_path / "bpm.db")
    Path(config_mod.settings_file_path(db_path)).write_text(json.dumps({"ecosystem_secret": "f" * 40, "bpm_min": 70}))
    cfg = config_mod.load_settings_override({"db_path": db_path, "ecosystem_secret": ""})
    assert cfg["ecosystem_secret"] == "" and cfg["bpm_min"] == 70
