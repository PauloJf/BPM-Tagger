"""Smoke tests: every Jinja page renders for a logged-in user (guards url_for names)."""

import pytest


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess["csrf_token"] = "tok"
    client.post("/login", data={"csrf_token": "tok", "password": "s3cret"})
    return client


@pytest.mark.parametrize("path", ["/tracks", "/review", "/stats", "/about", "/settings"])
def test_pages_render(auth_client, path):
    resp = auth_client.get(path)
    assert resp.status_code == 200


def test_index_redirects_to_tracks(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 302
    assert "/tracks" in resp.headers["Location"]


def test_healthz_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_audio_requires_path(auth_client):
    assert auth_client.get("/audio").status_code == 400


def test_track_detail_missing_returns_404(auth_client):
    assert auth_client.get("/track?path=/music/nope.mp3").status_code == 404


def test_unauthenticated_page_redirects_to_login(client):
    resp = client.get("/tracks")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
