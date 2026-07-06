"""Serving model after the M2 SPA migration.

The browser UI is now a React bundle; Flask serves it and exposes only the JSON
API + /audio + /healthz. These tests guard that contract. (Auth, CSRF, lockout
and settings round-trips are covered in test_api_spa.py.)
"""

import pytest


@pytest.fixture
def auth_client(client):
    client.post("/api/login", json={"password": "s3cret"})
    return client


def test_healthz_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_audio_requires_path(auth_client):
    assert auth_client.get("/audio").status_code == 400


def test_protected_api_returns_401_not_redirect(client):
    # Unauthenticated API access is a JSON 401, never an HTML redirect, so the
    # SPA can handle it without following a redirect into the shell.
    resp = client.get("/api/tracks")
    assert resp.status_code == 401


def test_spa_catch_all_is_not_an_api_route(client):
    # A client-router path is never treated as an API 404/401 — it is served the
    # SPA shell (200) or, when the bundle isn't built in the test env, a 501
    # "not built" marker. Either way it must not be 401/404.
    resp = client.get("/tracks")
    assert resp.status_code in (200, 501)


def test_unknown_api_path_is_404(client):
    # Backend-owned prefixes never fall through to the SPA shell.
    resp = client.get("/api/does-not-exist")
    assert resp.status_code in (401, 404)
